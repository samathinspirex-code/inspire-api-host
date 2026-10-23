import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zipfile import ZipFile

from pydantic import ValidationError as SchemaError
from app.core.errors import ForbiddenError, ValidationError
from app.modules.cms import media_service
from app.modules.cms.schemas.media_asset import MediaUploadRequest
from app.modules.lms import content_service, presentation_service as service
from app.modules.lms.schemas.content import LearningItemCreate


def deck(extra=None):
    output = BytesIO()
    with ZipFile(output, 'w') as archive:
        archive.writestr('[Content_Types].xml', '<Types/>')
        archive.writestr('ppt/presentation.xml', '<presentation/>')
        if extra:
            archive.writestr(extra, b'macro')
    return output.getvalue()


class PresentationValidationTests(unittest.TestCase):
    def test_upload_and_content_schema(self):
        payload = MediaUploadRequest(filename='slides.pptx', name='slides',
                                     content_type=media_service.PPTX_CONTENT_TYPE,
                                     size_bytes=100, folder='course-materials')
        self.assertEqual(media_service.allowed_upload_type(payload), ('document', '.pptx'))
        with self.assertRaises(ValidationError):
            media_service.allowed_upload_type(payload.model_copy(update={'content_type': 'application/pdf'}))
        with self.assertRaises(ValidationError):
            media_service.allowed_upload_type(payload.model_copy(update={'folder': 'media-library'}))
        self.assertEqual(LearningItemCreate(item_type='pptx', title='Slides', resource_url='https://example.com/slides.pptx').item_type, 'pptx')
        with self.assertRaises(SchemaError):
            LearningItemCreate(item_type='pptx', title='Slides')

    def test_rejects_invalid_and_macro_files(self):
        service.validate_pptx(deck())
        for data in (b'', b'not a pptx', deck('ppt/vbaProject.bin')):
            with self.subTest(data=data[:20]), self.assertRaises(ValidationError):
                service.validate_pptx(data)

    def test_libreoffice_generates_pdf_with_isolated_profile(self):
        def convert(command, **kwargs):
            self.assertIn('pdf:impress_pdf_Export', command)
            self.assertTrue(any(value.startswith('-env:UserInstallation=file:') for value in command))
            (Path(kwargs['cwd']) / 'slides.pdf').write_bytes(b'%PDF-test')
            return SimpleNamespace(returncode=0)
        with patch.object(service, '_libreoffice_path', return_value='soffice'), patch.object(service.subprocess, 'run', side_effect=convert):
            self.assertEqual(service.convert_pptx_to_pdf(deck()), b'%PDF-test')


class PresentationAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_cached_preview_skips_conversion(self):
        with patch.object(service.media_service, '_client', return_value=MagicMock()), patch.object(service, '_cached_preview', return_value=b'%PDF-cached'), patch.object(service, 'convert_pptx_to_pdf') as convert:
            self.assertEqual(await service.ensure_presentation_preview(SimpleNamespace(bucket='test', object_key='slides.pptx')), b'%PDF-cached')
            convert.assert_not_called()

    async def test_missing_preview_is_converted_and_cached(self):
        client = MagicMock()
        with patch.object(service.media_service, '_client', return_value=client), patch.object(service, '_cached_preview', return_value=None), patch.object(service, '_read_object', return_value=deck()), patch.object(service, 'convert_pptx_to_pdf', return_value=b'%PDF-generated'):
            await service.ensure_presentation_preview(SimpleNamespace(bucket='test', object_key='slides.pptx'))
        client.put_object.assert_called_once_with(Bucket='test', Key='slides.pptx.preview.pdf', Body=b'%PDF-generated', ContentType='application/pdf', ContentDisposition='inline')

    async def test_locked_student_cannot_load_preview(self):
        item = SimpleNamespace(item_type='pptx', resource_url='https://example.com/slides.pptx', module_id=2)
        db = SimpleNamespace(get=AsyncMock(side_effect=[item, SimpleNamespace(course_id=3)]), commit=AsyncMock())
        with patch.object(content_service, 'get_accessible_student_item', new=AsyncMock(side_effect=ForbiddenError('Locked'))), patch.object(service, 'read_presentation_preview', new=AsyncMock()) as render:
            with self.assertRaises(ForbiddenError):
                await content_service.read_learning_item_pdf(db, 1, 7, 'STUDENT')
            render.assert_not_called()

    async def test_authorized_student_loads_rendered_preview(self):
        item = SimpleNamespace(item_type='pptx', resource_url='https://example.com/slides.pptx', module_id=2)
        db = SimpleNamespace(get=AsyncMock(side_effect=[item, SimpleNamespace(course_id=3)]), commit=AsyncMock())
        with patch.object(content_service, 'get_accessible_student_item', new=AsyncMock()), patch.object(service, 'read_presentation_preview', new=AsyncMock(return_value=b'%PDF-slides')) as render:
            self.assertEqual(await content_service.read_learning_item_pdf(db, 1, 7, 'STUDENT'), b'%PDF-slides')
            render.assert_awaited_once_with(db, item.resource_url)
