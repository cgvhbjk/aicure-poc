import React, { useState, useEffect, useMemo, useRef } from 'react'
import TrialsTable from './components/TrialsTable'
import NewsTable from './components/NewsTable'
import ViewsSidebar from './components/ViewsSidebar'
import { getStats } from './api'
import './App.css'

function useDebounce(value, delay) {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(t)
  }, [value, delay])
  return debounced
}

function CheckGroup({ label, options, selected, onToggle, labelFn }) {
  return (
    <div className="filter-group">
      <div className="filter-label">{label}</div>
      {options.map((opt) => (
        <label key={opt} className="checkbox-label">
          <input
            type="checkbox"
            checked={selected.includes(opt)}
            onChange={() => onToggle(opt)}
          />
          {labelFn ? labelFn(opt) : opt}
        </label>
      ))}
    </div>
  )
}

const STATUSES = ['RECRUITING', 'NOT_YET_RECRUITING', 'ACTIVE_NOT_RECRUITING', 'COMPLETED']
const PHASES = ['PHASE1', 'PHASE2', 'PHASE3', 'PHASE4']
const SOURCES = [
  'Fierce Pharma', 'Endpoints News', 'PharmaVoice',
  'TrialSite News', 'BioPharma Dive', 'STAT News', 'BioSpace',
  'Google News — GLP-1', 'Google News — Semaglutide', 'Google News — Tirzepatide',
  'Google News — Obesity trial', 'Google News — Weight loss', 'Google News — T2D trial',
  'Google News — Heart failure', 'Google News — A-fib trial',
  'Google News — First patient', 'Google News — IND filing',
]

export default function App() {
  const [activeTab, setActiveTab] = useState('trials')
  const [stats, setStats] = useState(null)
  const [filterOpen, setFilterOpen] = useState(false)

  const [searchText, setSearchText] = useState('')
  const [selectedStatuses, setSelectedStatuses] = useState([])
  const [selectedPhases, setSelectedPhases] = useState([])
  const [selectedAreas, setSelectedAreas] = useState([])
  const [hasNewsOnly, setHasNewsOnly] = useState(false)

  const [selectedSources, setSelectedSources] = useState([])
  const [selectedRegistries, setSelectedRegistries] = useState([])
  const [linkedOnly, setLinkedOnly] = useState(null)
  const [announcementsOnly, setAnnouncementsOnly] = useState(false)
  const [resultsOnly, setResultsOnly] = useState(false)

  // Ref to the trials grid API, shared with ViewsSidebar
  const trialApiRef = useRef(null)

  const debouncedSearch = useDebounce(searchText, 400)

  useEffect(() => {
    getStats().then((r) => setStats(r.data)).catch(console.error)
  }, [])

  const therapeuticAreas = stats ? Object.keys(stats.by_therapeutic_area || {}).sort() : []

  const toggle = (setFn) => (item) =>
    setFn((prev) => (prev.includes(item) ? prev.filter((x) => x !== item) : [...prev, item]))

  const trialFilters = useMemo(
    () => ({
      q: debouncedSearch || undefined,
      status: selectedStatuses.length ? selectedStatuses : undefined,
      phase: selectedPhases.length ? selectedPhases : undefined,
      therapeutic_area: selectedAreas.length ? selectedAreas : undefined,
      has_news: hasNewsOnly || undefined,
      registry: selectedRegistries.length ? selectedRegistries : undefined,
    }),
    [debouncedSearch, selectedStatuses, selectedPhases, selectedAreas, hasNewsOnly, selectedRegistries]
  )

  const newsFilters = useMemo(
    () => ({
      q: debouncedSearch || undefined,
      source: selectedSources.length ? selectedSources : undefined,
      linked_only: linkedOnly,
      is_trial_announcement: announcementsOnly || undefined,
      is_trial_results: resultsOnly || undefined,
    }),
    [debouncedSearch, selectedSources, linkedOnly, announcementsOnly, resultsOnly]
  )

  const clearFilters = () => {
    setSearchText('')
    setSelectedStatuses([])
    setSelectedPhases([])
    setSelectedAreas([])
    setHasNewsOnly(false)
    setSelectedSources([])
    setSelectedRegistries([])
    setLinkedOnly(null)
    setAnnouncementsOnly(false)
    setResultsOnly(false)
  }

  const toggleFilter = () => setFilterOpen(v => !v)

  return (
    <div className="app">
      <div className="stats-bar">
        <span className="app-title">AiCure Clinical Intelligence</span>
        {stats ? (
          <div className="stats-pills">
            <span className="stat-pill">{stats.total_trials.toLocaleString()} trials</span>
            <span className="stat-pill accent">{stats.trials_with_news} with news</span>
            <span className="stat-pill">{stats.total_news} news items</span>
            {stats.eu_ctis_count > 0 && (
              <span className="stat-pill eu">CTIS {stats.eu_ctis_count}</span>
            )}
            {stats.eu_ctr_count > 0 && (
              <span className="stat-pill eu">EU-CTR {stats.eu_ctr_count}</span>
            )}
            {stats.last_ingested && (
              <span className="stat-pill muted">
                Ingested {stats.last_ingested.slice(0, 10)}
              </span>
            )}
          </div>
        ) : (
          <span className="stat-pill muted">Connecting to API…</span>
        )}
      </div>

      <div className="tab-bar">
        <button
          className={`tab-btn${activeTab === 'trials' ? ' active' : ''}`}
          onClick={() => setActiveTab('trials')}
        >
          Trials
        </button>
        <button
          className={`tab-btn${activeTab === 'news' ? ' active' : ''}`}
          onClick={() => setActiveTab('news')}
        >
          News
        </button>
      </div>

      <div className="main-layout">
        {/* Views sidebar — always visible, left */}
        <ViewsSidebar gridApiRef={trialApiRef} />

        <div className="app-body">
          {/* Filter sidebar — toggled by Filter button in each table's toolbar */}
          {filterOpen && (
            <div className="filter-sidebar">
              <div className="filter-sidebar-inner">
                <div className="filter-group">
                  <div className="filter-label">Search</div>
                  <input
                    className="search-input"
                    type="text"
                    placeholder="Title, sponsor, drug…"
                    value={searchText}
                    onChange={(e) => setSearchText(e.target.value)}
                  />
                </div>

                {activeTab === 'trials' && (
                  <>
                    <CheckGroup
                      label="Status"
                      options={STATUSES}
                      selected={selectedStatuses}
                      onToggle={toggle(setSelectedStatuses)}
                      labelFn={(s) => s.replace(/_/g, ' ')}
                    />
                    <CheckGroup
                      label="Phase"
                      options={PHASES}
                      selected={selectedPhases}
                      onToggle={toggle(setSelectedPhases)}
                      labelFn={(p) => p.replace('PHASE', 'Phase ')}
                    />
                    {therapeuticAreas.length > 0 && (
                      <CheckGroup
                        label="Therapeutic Area"
                        options={therapeuticAreas}
                        selected={selectedAreas}
                        onToggle={toggle(setSelectedAreas)}
                      />
                    )}
                    <div className="filter-group">
                      <label className="checkbox-label">
                        <input
                          type="checkbox"
                          checked={hasNewsOnly}
                          onChange={() => setHasNewsOnly((v) => !v)}
                        />
                        Has linked news only
                      </label>
                    </div>
                    <CheckGroup
                      label="Registry Source"
                      options={['ClinicalTrials.gov', 'CTIS', 'EU-CTR']}
                      selected={selectedRegistries}
                      onToggle={toggle(setSelectedRegistries)}
                    />
                  </>
                )}

                {activeTab === 'news' && (
                  <>
                    <CheckGroup
                      label="Source"
                      options={SOURCES}
                      selected={selectedSources}
                      onToggle={toggle(setSelectedSources)}
                    />
                    <div className="filter-group">
                      <label className="checkbox-label">
                        <input
                          type="checkbox"
                          checked={announcementsOnly}
                          onChange={() => { setAnnouncementsOnly((v) => !v); setResultsOnly(false) }}
                        />
                        New trial announcements ★
                      </label>
                      <label className="checkbox-label">
                        <input
                          type="checkbox"
                          checked={resultsOnly}
                          onChange={() => { setResultsOnly((v) => !v); setAnnouncementsOnly(false) }}
                        />
                        Trial results / findings ●
                      </label>
                    </div>

                    <div className="filter-group">
                      <div className="filter-label">Linked status</div>
                      {[['All', null], ['Linked', true], ['Unlinked', false]].map(([lbl, val]) => (
                        <label key={lbl} className="checkbox-label">
                          <input
                            type="radio"
                            checked={linkedOnly === val}
                            onChange={() => setLinkedOnly(val)}
                          />
                          {lbl}
                        </label>
                      ))}
                    </div>
                  </>
                )}

                <button className="btn-clear" onClick={clearFilters}>
                  Clear all filters
                </button>
              </div>
            </div>
          )}

          <div className="content">
            {activeTab === 'trials' && (
              <TrialsTable
                filters={trialFilters}
                filterOpen={filterOpen}
                onToggleFilter={toggleFilter}
                onGridReady={(api) => { trialApiRef.current = api }}
              />
            )}
            {activeTab === 'news' && (
              <NewsTable
                filters={newsFilters}
                filterOpen={filterOpen}
                onToggleFilter={toggleFilter}
              />
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
