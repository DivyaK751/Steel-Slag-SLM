import { useState } from 'react'

import {
  BIGQUERY_PROJECT,
  BIGQUERY_TABLES,
  LAST_SYNC,
  RESEARCH_PAPERS,
  type BigQueryTable,
  type ResearchPaper,
} from '../placeholder/sampleData'
import { DatabaseIcon, FileIcon, SearchIcon, UploadIcon } from './Icons'

type Tab = 'tables' | 'papers'

const formatCount = (count: number) => count.toLocaleString('en-US')
const tableId = (table: BigQueryTable) => `${table.dataset}.${table.name}`

function matches(query: string, ...fields: string[]): boolean {
  const needle = query.trim().toLowerCase()
  return fields.some((field) => field.toLowerCase().includes(needle))
}

function StatCard({ label, value, detail }: { label: string; value: string | number; detail: string }) {
  return (
    <div className="stat-card">
      <span>{label}</span>
      <strong>{value}</strong>
      <span>{detail}</span>
    </div>
  )
}

function TableDetail({ table }: { table: BigQueryTable }) {
  return (
    <section className="source-detail">
      <header>
        <h3 className="mono">{`${BIGQUERY_PROJECT}.${tableId(table)}`}</h3>
        <p>{table.description}</p>
      </header>
      <div className="preview-scroll">
        <table className="preview-table">
          <thead>
            <tr>
              {table.columns.map((column) => (
                <th key={column.name}>
                  {column.name}
                  <span>{column.type}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.previewRows.map((row) => (
              <tr key={String(row[0])}>
                {row.map((cell, index) => (
                  <td key={table.columns[index].name}>{cell}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <footer>Preview of first {table.previewRows.length} rows</footer>
    </section>
  )
}

function PaperDetail({ paper }: { paper: ResearchPaper }) {
  return (
    <section className="source-detail">
      <header>
        <h3>{paper.title}</h3>
        <p>
          {paper.authors.join(', ')} · {paper.year}
        </p>
      </header>
      <dl className="paper-facts">
        <dt>Summary</dt>
        <dd>{paper.summary}</dd>
        <dt>Indexed passages</dt>
        <dd>{formatCount(paper.passageCount)}</dd>
        <dt>Drive file</dt>
        <dd className="mono">{paper.driveFile}</dd>
        <dt>Added</dt>
        <dd>{paper.added}</dd>
      </dl>
    </section>
  )
}

/** The BigQuery tables and Drive papers the assistant searches, with a preview of each. */
export function KnowledgeSources() {
  const [tab, setTab] = useState<Tab>('tables')
  const [query, setQuery] = useState('')
  const [selectedTable, setSelectedTable] = useState(tableId(BIGQUERY_TABLES[0]))
  const [selectedPaper, setSelectedPaper] = useState(RESEARCH_PAPERS[0].id)

  const tables = BIGQUERY_TABLES.filter((table) => matches(query, tableId(table), table.description))
  const papers = RESEARCH_PAPERS.filter((paper) => matches(query, paper.title, ...paper.authors))
  // Keep the detail pane on a visible item when the search hides the selected one
  const table = tables.find((item) => tableId(item) === selectedTable) ?? tables[0]
  const paper = papers.find((item) => item.id === selectedPaper) ?? papers[0]

  const totalPassages = RESEARCH_PAPERS.reduce((sum, item) => sum + item.passageCount, 0)
  const totalRows = BIGQUERY_TABLES.reduce((sum, item) => sum + item.rowCount, 0)

  return (
    <main className="page">
      <header className="page-header">
        <div>
          <h1>Knowledge sources</h1>
          <p>The data and literature the assistant searches and cites.</p>
        </div>
        <button type="button" className="button-primary" disabled title="Coming soon">
          <UploadIcon /> Add source
        </button>
      </header>

      <div className="stat-row">
        <StatCard label="Research papers" value={RESEARCH_PAPERS.length} detail={`${formatCount(totalPassages)} passages`} />
        <StatCard label="BigQuery tables" value={BIGQUERY_TABLES.length} detail={`${formatCount(totalRows)} rows`} />
        <StatCard label="Last sync" value={LAST_SYNC.ago} detail={LAST_SYNC.schedule} />
      </div>

      <div className="sources-toolbar">
        <div className="tabs" role="tablist">
          <button type="button" role="tab" aria-selected={tab === 'tables'} onClick={() => setTab('tables')}>
            <DatabaseIcon /> BigQuery tables
          </button>
          <button type="button" role="tab" aria-selected={tab === 'papers'} onClick={() => setTab('papers')}>
            <FileIcon /> Research papers
          </button>
        </div>
        <label className="search-box">
          <SearchIcon />
          <input placeholder="Search sources" value={query} onChange={(event) => setQuery(event.target.value)} />
        </label>
      </div>

      <div className="sources-body">
        <div className="source-list">
          {tab === 'tables' &&
            tables.map((item) => (
              <button
                key={tableId(item)}
                type="button"
                className={item === table ? 'source-card is-active' : 'source-card'}
                onClick={() => setSelectedTable(tableId(item))}
              >
                <span className="mono muted">{item.dataset}</span>
                <strong className="mono">{item.name}</strong>
                <span className="muted">
                  {formatCount(item.rowCount)} rows · {item.updated}
                </span>
              </button>
            ))}
          {tab === 'papers' &&
            papers.map((item) => (
              <button
                key={item.id}
                type="button"
                className={item === paper ? 'source-card is-active' : 'source-card'}
                onClick={() => setSelectedPaper(item.id)}
              >
                <span className="muted">
                  {item.authors[0]} · {item.year}
                </span>
                <strong>{item.title}</strong>
                <span className="muted">
                  {formatCount(item.passageCount)} passages · {item.added}
                </span>
              </button>
            ))}
          {(tab === 'tables' ? tables : papers).length === 0 && <p className="muted">No sources match “{query}”.</p>}
        </div>
        {tab === 'tables' && table && <TableDetail table={table} />}
        {tab === 'papers' && paper && <PaperDetail paper={paper} />}
      </div>
    </main>
  )
}
