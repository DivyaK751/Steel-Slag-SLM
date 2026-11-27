// Placeholder data for screens not yet wired to the backend (chat history and knowledge
// sources). Each export here is meant to be replaced by an API call.

import type { Citation } from '../api/types'

export const SUGGESTED_QUESTIONS = [
  'Compare CO₂ uptake of BOF vs EAF slag in our lab runs',
  'What limits carbonation kinetics at ambient pressure?',
  'Is carbonated slag safe as a cement substitute?',
]

export interface SampleChat {
  id: string
  title: string
  when: string
  question: string
  answer: string // markdown with [P#] labels, as the real answer writer produces
  citations: Citation[]
}

const HUIJGEN: Citation = {
  label: 'P1',
  paper_id: 'huijgen_2005',
  title: 'Mineral CO₂ sequestration by steel slag carbonation',
  authors: ['W. J. J. Huijgen', 'G.-J. Witkamp', 'R. N. J. Comans'],
  year: 2005,
  doi: '10.1021/es050795f',
  pages: [9678],
}

const PAN: Citation = {
  label: 'P2',
  paper_id: 'pan_2017',
  title: 'CO₂ capture by accelerated carbonation of alkaline wastes',
  authors: ['S.-Y. Pan', 'E. E. Chang', 'P.-C. Chiang'],
  year: 2017,
  doi: null,
  pages: [12],
}

export const SAMPLE_CHATS: SampleChat[] = [
  {
    id: 'bof-particle-size',
    title: 'BOF slag particle size vs CO₂ uptake',
    when: 'Today',
    question:
      "How much does particle size affect CO₂ uptake for BOF slag, and what's the realistic net removal per tonne?",
    answer:
      'Particle size is the strongest lever for aqueous carbonation of BOF slag. Grinding from under 2 mm to ' +
      'under 38 µm raised conversion from **24% to 74%** in 30 minutes at 100 °C and 19 bar [P1]. Calcium has to ' +
      'diffuse through a growing calcite layer on each grain, so smaller grains give a shorter path [P1].\n\n' +
      'Your own lab data agrees: in `slag_lab.carbonation_runs`, BOF runs at 38 µm average **246 kg CO₂/t**, ' +
      'against 171 kg/t at 106 µm.\n\n' +
      '**Net removal** is lower once grinding and compression energy are counted: about **0.18–0.22 t CO₂ per ' +
      'tonne** of slag [P2].',
    citations: [HUIJGEN, PAN],
  },
  {
    id: 'ambient-weathering',
    title: 'Ambient weathering of slag heaps',
    when: 'Yesterday',
    question: 'How fast do outdoor slag heaps take up CO₂ without any processing?',
    answer:
      'Slowly. Passive weathering mostly carbonates the outer few centimetres of a heap, so uptake over years ' +
      'is a small fraction of the theoretical capacity [P1].',
    citations: [{ ...PAN, label: 'P1' }],
  },
  {
    id: 'cement-substitute',
    title: 'Carbonated slag as cement substitute',
    when: 'Nov 21',
    question: 'Is carbonated slag safe as a cement substitute?',
    answer:
      'Carbonation lowers free lime, which reduces the expansion that normally limits slag in concrete [P1]. ' +
      'Heavy-metal leaching still needs testing for each source.',
    citations: [HUIJGEN],
  },
  {
    id: 'vanadium-leaching',
    title: 'Vanadium leaching after carbonation',
    when: 'Nov 18',
    question: 'Does carbonation change vanadium leaching from BOF slag?',
    answer:
      'It can increase it. As pH falls after carbonation, vanadium becomes more mobile, so leachate limits ' +
      'should be checked after treatment [P1].',
    citations: [HUIJGEN],
  },
]

export interface BigQueryTable {
  dataset: string
  name: string
  description: string
  rowCount: number
  updated: string
  columns: { name: string; type: string }[]
  previewRows: (string | number)[][]
}

export const BIGQUERY_PROJECT = 'research-prod'

export const BIGQUERY_TABLES: BigQueryTable[] = [
  {
    dataset: 'slag_lab',
    name: 'carbonation_runs',
    description: 'Bench-scale carbonation experiments with process conditions and uptake.',
    rowCount: 18432,
    updated: '2 days ago',
    columns: [
      { name: 'run_id', type: 'STRING' },
      { name: 'slag_type', type: 'STRING' },
      { name: 'particle_um', type: 'INT64' },
      { name: 'temp_c', type: 'FLOAT64' },
      { name: 'pressure_bar', type: 'FLOAT64' },
      { name: 'co2_uptake_kg_t', type: 'FLOAT64' },
    ],
    previewRows: [
      ['R-1042', 'BOF', 38, 100, 19, 246.1],
      ['R-1043', 'BOF', 106, 100, 19, 171.4],
      ['R-1044', 'EAF', 38, 80, 10, 132.8],
      ['R-1045', 'Ladle', 63, 60, 1, 88.2],
    ],
  },
  {
    dataset: 'slag_lab',
    name: 'slag_composition',
    description: 'XRF oxide composition (wt%) for each slag sample.',
    rowCount: 2210,
    updated: '1 week ago',
    columns: [
      { name: 'sample_id', type: 'STRING' },
      { name: 'slag_type', type: 'STRING' },
      { name: 'cao_pct', type: 'FLOAT64' },
      { name: 'sio2_pct', type: 'FLOAT64' },
      { name: 'feo_pct', type: 'FLOAT64' },
      { name: 'mgo_pct', type: 'FLOAT64' },
    ],
    previewRows: [
      ['S-0201', 'BOF', 41.2, 12.8, 24.5, 6.1],
      ['S-0202', 'EAF', 30.4, 15.1, 31.7, 5.2],
      ['S-0203', 'Ladle', 52.3, 18.6, 1.4, 8.8],
      ['S-0204', 'BOF', 44.0, 11.9, 22.3, 7.0],
    ],
  },
  {
    dataset: 'carbon_accounting',
    name: 'lca_results',
    description: 'Life-cycle net CO₂ balance per process route.',
    rowCount: 640,
    updated: '3 weeks ago',
    columns: [
      { name: 'route_id', type: 'STRING' },
      { name: 'process', type: 'STRING' },
      { name: 'energy_kwh_t', type: 'FLOAT64' },
      { name: 'gross_t_co2_t', type: 'FLOAT64' },
      { name: 'net_t_co2_t', type: 'FLOAT64' },
    ],
    previewRows: [
      ['L-001', 'High-gravity', 112, 0.25, 0.21],
      ['L-002', 'Autoclave', 185, 0.26, 0.18],
      ['L-003', 'Ambient heap', 4, 0.03, 0.03],
      ['L-004', 'Slurry reactor', 140, 0.24, 0.19],
    ],
  },
]

export interface ResearchPaper {
  id: string
  title: string
  authors: string[]
  year: number
  passageCount: number
  driveFile: string
  added: string
  summary: string
}

export const RESEARCH_PAPERS: ResearchPaper[] = [
  {
    id: 'huijgen_2005',
    title: 'Mineral CO₂ sequestration by steel slag carbonation',
    authors: ['W. J. J. Huijgen', 'G.-J. Witkamp', 'R. N. J. Comans'],
    year: 2005,
    passageCount: 412,
    driveFile: 'Papers/huijgen_2005_slag_carbonation.pdf',
    added: '2 days ago',
    summary: 'Aqueous carbonation of steel slag: effect of particle size, temperature and CO₂ pressure.',
  },
  {
    id: 'pan_2017',
    title: 'CO₂ capture by accelerated carbonation of alkaline wastes',
    authors: ['S.-Y. Pan', 'E. E. Chang', 'P.-C. Chiang'],
    year: 2017,
    passageCount: 538,
    driveFile: 'Papers/pan_2017_accelerated_carbonation.pdf',
    added: '1 week ago',
    summary: 'Review of process routes, energy use and net capture for alkaline industrial wastes.',
  },
  {
    id: 'yildirim_2011',
    title: 'Use of steel slag in Portland cement concrete',
    authors: ['I. Z. Yildirim', 'M. Prezzi'],
    year: 2011,
    passageCount: 334,
    driveFile: 'Papers/yildirim_2011_slag_in_concrete.pdf',
    added: '3 weeks ago',
    summary: 'Volume stability, free lime and leaching of steel slag used as aggregate and binder.',
  },
]

export const LAST_SYNC = { ago: '2 days', schedule: 'nightly at 02:00 UTC' }

