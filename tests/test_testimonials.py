import unittest
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from app.core.database import get_db
from app.core.errors import APIError, api_error_handler
from app.modules.cms.testimonials import cms_router, public_router


class TestimonialsTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        self.connection = self.engine.connect()
        self.connection.connection.create_function('now', 0, lambda: datetime.now(timezone.utc).isoformat())
        self.connection.execute(text('''CREATE TABLE cms_testimonials (
          testimonial_id INTEGER PRIMARY KEY, name TEXT, programme TEXT, caption TEXT,
          video_url TEXT, thumbnail_url TEXT, position INTEGER, status TEXT, updated_at TEXT)'''))
        connection = self.connection

        class Database:
            async def execute(self, statement, parameters=None):
                return connection.execute(statement, parameters or {})

            async def commit(self):
                connection.commit()

        async def database():
            yield Database()

        self.app = FastAPI()
        self.app.add_exception_handler(APIError, api_error_handler)
        self.app.include_router(cms_router)
        self.app.include_router(public_router)
        self.app.dependency_overrides[get_db] = database
        self.auth_dependency = cms_router.dependencies[0].dependency
        self.app.dependency_overrides[self.auth_dependency] = lambda: object()
        self.client = TestClient(self.app)
        self.payload = dict(name='Student', programme='HND', caption='A student story.',
            video_url='/testimonials/student.mp4', thumbnail_url='/testimonials/student.jpg', position=2, status='Draft')

    def tearDown(self):
        self.client.close()
        self.connection.close()
        self.engine.dispose()

    def test_draft_publish_edit_unpublish_and_delete(self):
        created = self.client.post('/api/v1/cms/testimonials', json=self.payload)
        self.assertEqual(created.status_code, 201)
        item_id = created.json()['testimonial_id']
        self.assertEqual(self.client.get('/api/v1/public/testimonials').json()['data'], [])
        changed = {**self.payload, 'name': 'Updated student', 'status': 'Published'}
        self.assertEqual(self.client.put(f'/api/v1/cms/testimonials/{item_id}', json=changed).status_code, 200)
        self.assertEqual(self.client.get('/api/v1/public/testimonials').json()['data'][0]['name'], 'Updated student')
        self.client.put(f'/api/v1/cms/testimonials/{item_id}', json=self.payload)
        self.assertEqual(self.client.get('/api/v1/public/testimonials').json()['data'], [])
        self.assertEqual(self.client.delete(f'/api/v1/cms/testimonials/{item_id}').status_code, 204)
        self.assertEqual(self.client.get('/api/v1/cms/testimonials').json()['data'], [])

    def test_public_order_and_hidden_drafts(self):
        for name, position, status in [('Second', 2, 'Published'), ('First', 1, 'Published'), ('Hidden', 0, 'Draft')]:
            self.client.post('/api/v1/cms/testimonials', json={**self.payload, 'name': name, 'position': position, 'status': status})
        self.assertEqual([r['name'] for r in self.client.get('/api/v1/public/testimonials').json()['data']], ['First', 'Second'])

    def test_rejects_unsafe_urls_and_blank_names(self):
        for change in [{'video_url': 'javascript:alert(1)'}, {'thumbnail_url': '//example.com/x'}, {'name': '   '}, {'position': -1}]:
            self.assertEqual(self.client.post('/api/v1/cms/testimonials', json={**self.payload, **change}).status_code, 422)

    def test_cms_requires_authentication(self):
        del self.app.dependency_overrides[self.auth_dependency]
        self.assertIn(self.client.post('/api/v1/cms/testimonials', json=self.payload).status_code, (401, 403))


if __name__ == '__main__':
    unittest.main()
