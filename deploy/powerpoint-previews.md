# PowerPoint course previews

PPTX learning items keep their original upload and render a PDF preview for the
existing fullscreen lesson viewer. The final slide uses the same durable
completion save as PDF materials. Slides are static; animations and embedded
PowerPoint playback are not part of the preview.

## Linux API setup

Install LibreOffice Impress and fonts before enabling uploads on the hosted API:

```sh
sudo apt-get update
sudo apt-get install -y libreoffice-impress fonts-liberation fonts-crosextra-carlito
```

The API discovers `libreoffice` or `soffice` on PATH. If installed elsewhere, set
`LIBREOFFICE_PATH` to its executable path. The API service user needs a writable
temporary directory. Each conversion has its own LibreOffice profile, runs
without a window, and has a two-minute timeout. Configure the reverse proxy's
request timeout to allow at least 180 seconds for upload completion.

Deploy the API and LMS UI together. Expand the learning item type constraint:

```sh
python scripts/apply_powerpoint_content_migration.py
```

The migration is safe to rerun and preserves existing content.

## Storage and access

- PPTX uploads are limited to 50 MB in the `course-materials` folder.
- The preview is cached beside the original under `<original-key>.preview.pdf`.
- Storage permissions must allow the API to read the source and write/read the
  preview. No external document hosting service receives the presentation.
- Preview requests check the student's released content access or the staff
  course permissions before returning the rendered PDF.
- Upload completion prepares the preview so students normally read a cached
  version. Missing previews can be recreated on the next authorized view.

For local Windows development, installed PowerPoint can provide conversion when
LibreOffice is absent. LibreOffice is the supported hosted server converter.

Reference: [LibreOffice PDF export parameters](https://help.libreoffice.org/latest/en-US/text/shared/guide/pdf_params.html).
