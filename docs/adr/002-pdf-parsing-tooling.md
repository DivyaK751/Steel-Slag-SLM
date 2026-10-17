# 002: Parse Drive PDFs with Docling and PyMuPDF

**Status:** accepted, 2026-10-17

## Context

The source PDFs already live in the team's shared Google Drive folder. The parsing step needs to
extract text, tables, and embedded images, and make the results easy for the next pipeline
component to discover. The experiment notebook showed that PyMuPDF image resources are not
always complete figures: a diagram can be split across several resources, and a PDF can contain
publisher artwork that is not relevant content.

## Decision

- Read PDFs from the existing Drive folder identified by `DRIVE_FOLDER_ID`. Do not fetch them
  again from OpenAlex or require users to download them locally.
- Use Docling for text and tables, and PyMuPDF for embedded image resources.
- Keep each extracted image resource separate and preserve its source page and bounding box.
  Do not automatically merge fragments into figures; downstream consumers can group or render
  them using their source locations.
- Keep extraction inclusive of all pages. Consumers can exclude pages such as page 1 using the
  recorded page metadata when that is appropriate for their task.
- Write outputs to a `_parsed` subfolder inside the existing Drive paper folder:
  - `_parsed/index.json` is the Drive-only registry, mapping each paper ID to its source PDF and
    parsed JSON file.
  - `_parsed/<paper_id>/parsed.json` contains the parsed document and references its extracted
    image files.
  - `_parsed/<paper_id>/images/` contains the extracted image resources.
- Store Drive file IDs and links in the index and parsed JSON so another component can retrieve
  artifacts without searching Drive by filename.
- Update the index only after a paper's JSON and image files have been uploaded successfully.
  Keep parsing runs single-writer so concurrent runs cannot overwrite each other's index updates.
- Do not add parsing status or output links to BigQuery; Drive JSON files are the registry.

## Consequences

- The parser remains independent of Drive: it accepts PDF bytes and returns parsed records.
  The pipeline handles listing, downloading, uploading, and maintaining the index.
- The JSON schema must preserve the paper ID, source PDF identity, parser names, parsed text and
  tables, and image placement metadata.
- A partially uploaded paper must not appear as successfully parsed in `index.json`; a later run
  can retry it.
- The index is a Drive file, so downstream components need permission to read the shared folder.