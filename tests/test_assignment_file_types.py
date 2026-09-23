import unittest

from app.core.errors import ValidationError
from app.modules.cms.media_service import allowed_upload_type
from app.modules.cms.schemas.media_asset import MediaUploadRequest


def upload(filename: str, content_type: str, folder: str = "assignment-submissions"):
    return MediaUploadRequest(
        filename=filename, name="sample-file", content_type=content_type,
        size_bytes=1024, folder=folder,
    )


class AssignmentFileTypeTests(unittest.TestCase):
    def test_office_zip_and_existing_formats_allowed_for_assignments(self):
        cases = {
            "work.docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "slides.pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "sheet.xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "records.csv": "text/csv",
            "archive.zip": "application/zip",
            "paper.pdf": "application/pdf",
            "photo.jpeg": "image/jpeg",
        }
        for filename, content_type in cases.items():
            with self.subTest(filename=filename):
                self.assertEqual(allowed_upload_type(upload(filename, content_type))[1], ".jpg" if filename.endswith(".jpeg") else f".{filename.split('.')[-1]}")
                self.assertIn(allowed_upload_type(upload(filename, content_type, "assignment-materials"))[0], {"image", "document"})

    def test_assignment_extensions_are_not_enabled_in_media_library(self):
        with self.assertRaises(ValidationError):
            allowed_upload_type(upload("archive.zip", "application/zip", "media-library"))

    def test_mismatched_or_unsafe_types_are_rejected(self):
        for filename, content_type in (
            ("slides.pptx", "application/zip"),
            ("script.exe", "application/pdf"),
            ("video.mp4", "video/mp4"),
        ):
            with self.subTest(filename=filename), self.assertRaises(ValidationError):
                allowed_upload_type(upload(filename, content_type))


if __name__ == "__main__":
    unittest.main()
