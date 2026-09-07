import unittest
from unittest.mock import patch

from core import taobao_downloader
from gui.video_download_tab import extract_urls_from_text


class XiaohongshuDownloaderTests(unittest.TestCase):
    def test_detects_share_and_direct_note_links(self):
        cases = [
            "https://xhslink.cn/o/9ifsMDZlP9S",
            "https://www.xiaohongshu.com/explore/696b78c4000000002103f1ab?type=video",
            "https://www.xiaohongshu.com/discovery/item/696b78c4000000002103f1ab",
        ]

        for url in cases:
            with self.subTest(url=url):
                self.assertEqual(
                    taobao_downloader.detect_link_type(url),
                    taobao_downloader.LINK_TYPE_XIAOHONGSHU,
                )

    def test_does_not_detect_spoofed_or_unrelated_xiaohongshu_urls(self):
        cases = [
            "https://xhslink.cn.evil.test/o/9ifsMDZlP9S",
            "https://example.com/?next=https://www.xiaohongshu.com/explore/note",
            "https://www.xiaohongshu.com/user/profile/123",
        ]

        for url in cases:
            with self.subTest(url=url):
                self.assertNotEqual(
                    taobao_downloader.detect_link_type(url),
                    taobao_downloader.LINK_TYPE_XIAOHONGSHU,
                )

    def test_extracts_h264_master_and_backup_before_h265(self):
        html = r'''
        <script>
        {"stream":{"h264":[{
          "masterUrl":"http:\u002F\u002Fsns-video-v6.xhscdn.com\u002Fh264.mp4?sign=abc\u0026t=1",
          "backupUrls":["http:\u002F\u002Fsns-bak-v1.xhscdn.com\u002Fh264.mp4"]
        }],"h265":[{
          "masterUrl":"http:\u002F\u002Fsns-video-v6.xhscdn.com\u002Fh265.mp4"
        }]}}
        </script>
        '''

        urls = taobao_downloader._extract_xiaohongshu_video_urls(html)

        self.assertEqual(
            urls,
            [
                "https://sns-video-v6.xhscdn.com/h264.mp4?sign=abc&t=1",
                "https://sns-bak-v1.xhscdn.com/h264.mp4",
                "https://sns-video-v6.xhscdn.com/h265.mp4",
            ],
        )

    def test_download_video_dispatches_xiaohongshu_links(self):
        with patch.object(
            taobao_downloader,
            "_download_xiaohongshu",
            return_value=(True, "xhs_video.mp4"),
        ) as download:
            result = taobao_downloader.download_video(
                "https://www.xiaohongshu.com/explore/696b78c4000000002103f1ab",
                "downloads",
            )

        self.assertEqual(result, (True, "xhs_video.mp4"))
        download.assert_called_once()

    def test_gui_extracts_complete_xiaohongshu_url_from_share_text(self):
        url = (
            "https://www.xiaohongshu.com/explore/696b78c4000000002103f1ab"
            "?type=video&xsec_token=abc_123="
        )

        self.assertEqual(
            extract_urls_from_text(f"复制笔记链接 {url} 打开小红书查看"),
            [url],
        )

    def test_reports_the_candidate_that_actually_downloaded(self):
        info = []
        with patch.object(
            taobao_downloader,
            "extract_xiaohongshu_video",
            return_value=(
                ["https://cdn.test/failed.mp4", "https://cdn.test/success.mp4"],
                "696b78c4000000002103f1ab",
                None,
                "https://www.xiaohongshu.com/explore/696b78c4000000002103f1ab",
            ),
        ), patch.object(
            taobao_downloader,
            "download_file",
            side_effect=[(False, "failed"), (True, None)],
        ), patch.object(taobao_downloader.os.path, "getsize", return_value=1024):
            success, _ = taobao_downloader._download_xiaohongshu(
                "https://xhslink.cn/o/9ifsMDZlP9S",
                "downloads",
                info_callback=lambda key, value: info.append((key, value)),
            )

        self.assertTrue(success)
        self.assertEqual(info, [("video_url", "https://cdn.test/success.mp4")])


if __name__ == "__main__":
    unittest.main()
