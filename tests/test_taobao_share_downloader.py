import unittest
from unittest.mock import patch

from core import taobao_downloader


GUANGGUANG_TARGET = (
    "https://web.m.taobao.com/app/tnode/web/index?tnode=page_guangguanghome"
    "&tabid=video&extParams=%7B%22contentId%22%3A%22519798057448%22%2C"
    "%22sceneSource%22%3A%22guang_share_shipin%22%7D"
)


class TaobaoShareDownloaderTests(unittest.TestCase):
    def test_detects_taobao_share_link_separately_from_product(self):
        self.assertEqual(
            taobao_downloader.detect_link_type(
                "https://e.tb.cn/h.8q2KpqS?tk=W7O4T6WKxHG"
            ),
            taobao_downloader.LINK_TYPE_TAOBAO_SHARE,
        )

    def test_extracts_guangguang_target_from_short_page(self):
        html = f"<script>var url = '{GUANGGUANG_TARGET}';</script>"

        self.assertEqual(
            taobao_downloader._extract_target_from_short_page(html),
            GUANGGUANG_TARGET,
        )

    def test_extracts_content_id_from_guangguang_target(self):
        self.assertEqual(
            taobao_downloader.extract_taobao_guangguang_content_id(
                GUANGGUANG_TARGET
            ),
            "519798057448",
        )

    def test_does_not_treat_product_content_id_as_guangguang(self):
        product_url = (
            "https://item.taobao.com/item.htm?id=1049810588754"
            "&contentId=519798057448"
        )

        self.assertIsNone(
            taobao_downloader.extract_taobao_guangguang_content_id(product_url)
        )

    def test_login_precheck_distinguishes_guangguang_and_product_shares(self):
        product_target = "https://item.taobao.com/item.htm?id=1049810588754"
        with patch.object(
            taobao_downloader,
            "resolve_short_url",
            side_effect=[GUANGGUANG_TARGET, product_target],
        ):
            self.assertFalse(
                taobao_downloader.taobao_url_requires_login(
                    "https://e.tb.cn/h.guangguang"
                )
            )
            self.assertTrue(
                taobao_downloader.taobao_url_requires_login(
                    "https://e.tb.cn/h.product"
                )
            )

    def test_prepares_share_targets_once_and_reports_login_requirement(self):
        product_target = "https://item.taobao.com/item.htm?id=1049810588754"
        links = [
            "https://e.tb.cn/h.guangguang",
            "https://e.tb.cn/h.product",
            "https://xhslink.cn/o/example",
        ]
        with patch.object(
            taobao_downloader,
            "resolve_short_url",
            side_effect=[GUANGGUANG_TARGET, product_target],
        ) as resolve:
            prepared, requires_login = taobao_downloader.prepare_download_links(links)

        self.assertEqual(
            prepared,
            [GUANGGUANG_TARGET, product_target, "https://xhslink.cn/o/example"],
        )
        self.assertTrue(requires_login)
        self.assertEqual(resolve.call_count, 2)

    def test_preflight_stops_before_resolving_remaining_links(self):
        stop_checks = iter([False, True])
        with patch.object(
            taobao_downloader,
            "resolve_short_url",
            return_value=GUANGGUANG_TARGET,
        ) as resolve:
            prepared, requires_login = taobao_downloader.prepare_download_links(
                ["https://e.tb.cn/h.first", "https://e.tb.cn/h.second"],
                should_stop=lambda: next(stop_checks),
            )

        self.assertEqual(prepared, [GUANGGUANG_TARGET])
        self.assertFalse(requires_login)
        resolve.assert_called_once_with("https://e.tb.cn/h.first")

    def test_share_link_downloads_by_content_id_without_product_login(self):
        with patch.object(
            taobao_downloader,
            "resolve_short_url",
            return_value=GUANGGUANG_TARGET,
        ), patch.object(
            taobao_downloader,
            "_download_taobao_content_id",
            return_value=(True, "taobao_video_519798057448.mp4"),
        ) as direct_download, patch.object(
            taobao_downloader,
            "extract_video_info",
        ) as product_extraction:
            result = taobao_downloader.download_video(
                "https://e.tb.cn/h.8q2KpqS?tk=W7O4T6WKxHG",
                "downloads",
            )

        self.assertEqual(result, (True, "taobao_video_519798057448.mp4"))
        direct_download.assert_called_once()
        product_extraction.assert_not_called()


if __name__ == "__main__":
    unittest.main()
