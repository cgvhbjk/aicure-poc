import React, { useState, useEffect, useMemo, useRef, useCallback } from 'react'
import TrialsTable from './components/TrialsTable'
import NewsTable from './components/NewsTable'
import OrgsTable from './components/OrgsTable'
import ViewsSidebar from './components/ViewsSidebar'
import DetailPanel from './components/DetailPanel'
import UploadModal from './components/UploadModal'
import MergeAuditView from './components/MergeAuditView'
import FundingTable from './components/FundingTable'
import GrantDetailPanel from './components/GrantDetailPanel'
import { compileConditions } from './utils/conditions'
import { getStats, getMergeStats, getGrantStats } from './api'
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

const SOURCES = [
  'Fierce Pharma', 'Endpoints News', 'PharmaVoice',
  'TrialSite News', 'BioPharma Dive', 'STAT News', 'BioSpace',
  'Google News — GLP-1', 'Google News — Semaglutide', 'Google News — Tirzepatide',
  'Google News — Obesity trial', 'Google News — Weight loss', 'Google News — T2D trial',
  'Google News — Heart failure', 'Google News — A-fib trial',
  'Google News — First patient', 'Google News — IND filing',
]
const ORG_TYPES = ['PHARMA', 'BIOTECH', 'CRO', 'DCT_VENDOR', 'DIGITAL_HEALTH', 'RPM', 'TELEHEALTH', 'ACADEMIC', 'GOVERNMENT', 'OTHER']

export default function App() {
  const [activeTab, setActiveTab] = useState('trials')
  const [activeOrgsSubTab, setActiveOrgsSubTab] = useState('table')
  const [stats, setStats] = useState(null)
  const [filterOpen, setFilterOpen] = useState(false)

  // Trial filters — Airtable-style conditions
  const [conditions, setConditions] = useState([])

  // News filters
  const [searchText, setSearchText] = useState('')
  const [selectedSources, setSelectedSources] = useState([])
  const [linkedOnly, setLinkedOnly] = useState(null)
  const [announcementsOnly, setAnnouncementsOnly] = useState(false)
  const [resultsOnly, setResultsOnly] = useState(false)

  // Org filters
  const [selectedOrgTypes, setSelectedOrgTypes] = useState([])
const [orgHasTrialsOnly, setOrgHasTrialsOnly] = useState(false)

  // Trial selected from org context (opens a top-level DetailPanel)
  const [orgSelectedTrial, setOrgSelectedTrial] = useState(null)

  // Upload modal + merge badge
  const [showUploadModal, setShowUploadModal] = useState(false)
  const [pendingMergeCount, setPendingMergeCount] = useState(null)

  // Grant stats
  const [grantStats, setGrantStats] = useState(null)
  // Grant detail panel opened from FundingTable
  const [selectedGrant, setSelectedGrant] = useState(null)

  const trialApiRef = useRef(null)
  // Bumped whenever AG Grid column/sort/filter state mutates, so ViewsSidebar
  // can react to changes that don't live in React state.
  const [gridStateBump, setGridStateBump] = useState(0)
  const bumpGridState = useCallback(() => setGridStateBump(v => v + 1), [])
  const debouncedSearch = useDebounce(searchText, 400)

  // Condition handlers
  const addCondition = useCallback((c) => setConditions(prev => [...prev, c]), [])
  const editCondition = useCallback((c) => setConditions(prev => prev.map(x => x.id === c.id ? c : x)), [])
  const removeCondition = useCallback((id) => setConditions(prev => prev.filter(x => x.id !== id)), [])
  const clearConditions = useCallback(() => setConditions([]), [])
  const getCurrentConditions = useCallback(() => conditions, [conditions])

  useEffect(() => {
    getStats().then((r) => setStats(r.data)).catch(console.error)
    getMergeStats().then((r) => setPendingMergeCount(r.data.pending)).catch(() => {})
    getGrantStats().then((r) => setGrantStats(r.data)).catch(() => {})
  }, [])

  const therapeuticAreas = stats ? Object.keys(stats.by_therapeutic_area || {}).sort() : []
  const countries = stats ? Object.keys(stats.by_country || {}) : []

  const toggle = (setFn) => (item) =>
    setFn((prev) => (prev.includes(item) ? prev.filter((x) => x !== item) : [...prev, item]))

  const { apiParams: trialFilters, agGridFilters } = useMemo(
    () => compileConditions(conditions),
    [conditions]
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

  const orgFilters = useMemo(
    () => ({
      q: debouncedSearch || undefined,
      org_type: selectedOrgTypes.length ? selectedOrgTypes : undefined,
      has_trials: orgHasTrialsOnly || undefined,
    }),
    [debouncedSearch, selectedOrgTypes, orgHasTrialsOnly]
  )

  const clearFilters = () => {
    setConditions([])
    setSearchText('')
    setSelectedSources([])
    setLinkedOnly(null)
    setAnnouncementsOnly(false)
    setResultsOnly(false)
    setSelectedOrgTypes([])
    setSelectedWhiteLabel([])
    setOrgHasTrialsOnly(false)
  }

  const toggleFilter = () => setFilterOpen(v => !v)

  const showFilterSidebar = filterOpen
    && activeTab !== 'trials'
    && activeTab !== 'merges'
    && activeTab !== 'funding'

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
            {grantStats && grantStats.total_grants > 0 && (
              <>
                <span className="stat-pill">{grantStats.total_grants.toLocaleString()} grants</span>
                <span className="stat-pill accent">{grantStats.active_grants} active</span>
                {grantStats.active_funding_usd > 0 && (
                  <span className="stat-pill" style={{ color: '#16a34a' }}>
                    ${(grantStats.active_funding_usd / 1_000_000).toFixed(1)}M funded
                  </span>
                )}
              </>
            )}
          </div>
        ) : (
          <span className="stat-pill muted">Connecting to API…</span>
        )}
        <div className="stats-bar-actions">
          <button
            className="btn-sm btn-upload"
            onClick={() => setShowUploadModal(true)}
          >
            Upload
          </button>
        </div>
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
        <button
          className={`tab-btn${activeTab === 'organizations' ? ' active' : ''}`}
          onClick={() => setActiveTab('organizations')}
        >
          Organizations
        </button>
        <button
          className={`tab-btn${activeTab === 'funding' ? ' active' : ''}`}
          onClick={() => setActiveTab('funding')}
        >
          Funding
        </button>
        <button
          className={`tab-btn${activeTab === 'merges' ? ' active' : ''}`}
          onClick={() => setActiveTab('merges')}
        >
          Merge Audit
          {pendingMergeCount > 0 && (
            <span className="tab-badge">{pendingMergeCount}</span>
          )}
        </button>
      </div>

      {/* Organizations sub-tab bar */}
      {activeTab === 'organizations' && (
        <div className="subtab-bar">
          <button
            className={`subtab-btn${activeOrgsSubTab === 'table' ? ' active' : ''}`}
            onClick={() => setActiveOrgsSubTab('table')}
          >
            Organizations
          </button>
        </div>
      )}

      <div className="main-layout">
        {/* Views sidebar — only for Trials tab */}
        {activeTab === 'trials' && (
          <ViewsSidebar
            tab="trials"
            gridApiRef={trialApiRef}
            getCurrentConditions={getCurrentConditions}
            onApplyConditions={setConditions}
            conditions={conditions}
            gridStateBump={gridStateBump}
          />
        )}

        <div className="app-body">
          {showFilterSidebar && (
            <div className="filter-sidebar">
              <div className="filter-sidebar-inner">
                <div className="filter-group">
                  <div className="filter-label">Search</div>
                  <input
                    className="search-input"
                    type="text"
                    placeholder="Search…"
                    value={searchText}
                    onChange={(e) => setSearchText(e.target.value)}
                  />
                </div>

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

                {activeTab === 'organizations' && activeOrgsSubTab === 'table' && (
                  <>
                    <CheckGroup
                      label="Org Type"
                      options={ORG_TYPES}
                      selected={selectedOrgTypes}
                      onToggle={toggle(setSelectedOrgTypes)}
                      labelFn={(t) => t.replace(/_/g, ' ')}
                    />
                    <div className="filter-group">
                      <label className="checkbox-label">
                        <input
                          type="checkbox"
                          checked={orgHasTrialsOnly}
                          onChange={() => setOrgHasTrialsOnly((v) => !v)}
                        />
                        Has trials only
                      </label>
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
                agGridFilters={agGridFilters}
                conditions={conditions}
                onAddCondition={addCondition}
                onEditCondition={editCondition}
                onRemoveCondition={removeCondition}
                onClearConditions={clearConditions}
                therapeuticAreas={therapeuticAreas}
                countries={countries}
                onGridReady={(api) => { trialApiRef.current = api }}
                onGridStateChange={bumpGridState}
              />
            )}
            {activeTab === 'news' && (
              <NewsTable
                filters={newsFilters}
                filterOpen={filterOpen}
                onToggleFilter={toggleFilter}
              />
            )}
            {activeTab === 'organizations' && activeOrgsSubTab === 'table' && (
              <OrgsTable
                filters={orgFilters}
                filterOpen={filterOpen}
                onToggleFilter={toggleFilter}
                onSelectTrial={setOrgSelectedTrial}
              />
            )}
            {activeTab === 'funding' && (
              <FundingTable onSelectTrial={setOrgSelectedTrial} />
            )}
            {activeTab === 'merges' && (
              <MergeAuditView />
            )}
          </div>
        </div>
      </div>

      {/* Trial detail opened from org context */}
      {orgSelectedTrial && (
        <DetailPanel trial={orgSelectedTrial} onClose={() => setOrgSelectedTrial(null)} />
      )}

      {showUploadModal && (
        <UploadModal
          onClose={() => setShowUploadModal(false)}
          onDone={() => {
            getMergeStats().then(r => setPendingMergeCount(r.data.pending)).catch(() => {})
          }}
        />
      )}
    </div>
  )
}
