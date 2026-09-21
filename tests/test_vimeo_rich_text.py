import unittest

from app.modules.lms.vimeo_service import _vimeo_description


class VimeoRichTextTests(unittest.TestCase):
    def test_formatted_lms_description_is_plain_text_on_vimeo(self):
        value = "<h2>Welcome &amp; overview</h2><p>Learn <strong>securely</strong>.</p><ol><li>First</li><li>Second</li></ol>"
        self.assertEqual(_vimeo_description(value), "Welcome & overview\nLearn securely.\nFirst\nSecond")

    def test_plain_description_remains_unchanged(self):
        self.assertEqual(_vimeo_description("A normal description"), "A normal description")
        self.assertIsNone(_vimeo_description(""))


if __name__ == "__main__":
    unittest.main()
