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
import ProgressBar from './components/ProgressBar'
import { compileConditions, compileNewsConditions, compileFundingConditions } from './utils/conditions'
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

// Per-tab condition list state + handlers. Three tabs (trials/news/funding)
// each track their own list, with identical add/edit/remove/clear semantics —
// this hook is the shared shape.
function useConditionList() {
  const [conditions, setConditions] = useState([])
  const add    = useCallback((c)  => setConditions(prev => [...prev, c]),                       [])
  const edit   = useCallback((c)  => setConditions(prev => prev.map(x => x.id === c.id ? c : x)), [])
  const remove = useCallback((id) => setConditions(prev => prev.filter(x => x.id !== id)),       [])
  const clear  = useCallback(()   => setConditions([]),                                          [])
  const getCurrent = useCallback(() => conditions, [conditions])
  return { conditions, setConditions, add, edit, remove, clear, getCurrent }
}

// Counter that ticks each time the grid mutates layout/sort/filter state —
// ViewsSidebar diffs against this to mark a view as modified.
function useGridStateBump() {
  const [value, setValue] = useState(0)
  const bump = useCallback(() => setValue(v => v + 1), [])
  return { value, bump }
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

const ORG_TYPES = ['PHARMA', 'BIOTECH', 'CRO', 'DCT_VENDOR', 'DIGITAL_HEALTH', 'RPM', 'TELEHEALTH', 'ACADEMIC', 'GOVERNMENT', 'OTHER']

export default function App() {
  const [activeTab, setActiveTab] = useState('trials')
  const [activeOrgsSubTab, setActiveOrgsSubTab] = useState('table')
  const [stats, setStats] = useState(null)
  const [filterOpen, setFilterOpen] = useState(false)

  // Per-tab condition lists
  const trials = useConditionList()
  const news = useConditionList()
  const funding = useConditionList()

  // Org filters (still simple checkbox sidebar)
  const [orgSearchText, setOrgSearchText] = useState('')
  const [selectedOrgTypes, setSelectedOrgTypes] = useState([])
  const [orgHasTrialsOnly, setOrgHasTrialsOnly] = useState(false)

  // Trial selected from org context (opens a top-level DetailPanel)
  const [orgSelectedTrial, setOrgSelectedTrial] = useState(null)

  // Upload modal + merge badge
  const [showUploadModal, setShowUploadModal] = useState(false)
  const [pendingMergeCount, setPendingMergeCount] = useState(null)

  // Grant stats
  const [grantStats, setGrantStats] = useState(null)

  // Grid API refs
  const trialApiRef = useRef(null)
  const newsApiRef = useRef(null)
  const fundingApiRef = useRef(null)

  // Per-tab grid-state bump counters (trigger ViewsSidebar auto-save)
  const trialGridBump = useGridStateBump()
  const newsGridBump = useGridStateBump()
  const fundingGridBump = useGridStateBump()

  const debouncedOrgSearch = useDebounce(orgSearchText, 400)

  useEffect(() => {
    getStats().then((r) => setStats(r.data)).catch(console.error)
    getMergeStats().then((r) => setPendingMergeCount(r.data.pending)).catch(console.error)
    getGrantStats().then((r) => setGrantStats(r.data)).catch(console.error)
  }, [])

  const therapeuticAreas = stats ? Object.keys(stats.by_therapeutic_area || {}).sort() : []
  const countries = stats ? Object.keys(stats.by_country || {}) : []

  const toggle = (setFn) => (item) =>
    setFn((prev) => (prev.includes(item) ? prev.filter((x) => x !== item) : [...prev, item]))

  const { apiParams: trialFilters } = useMemo(
    () => compileConditions(trials.conditions),
    [trials.conditions]
  )

  const { apiParams: newsFilters } = useMemo(
    () => compileNewsConditions(news.conditions),
    [news.conditions]
  )

  const { apiParams: fundingFilters } = useMemo(
    () => compileFundingConditions(funding.conditions),
    [funding.conditions]
  )

  const orgFilters = useMemo(
    () => ({
      q: debouncedOrgSearch || undefined,
      org_type: selectedOrgTypes.length ? selectedOrgTypes : undefined,
      has_trials: orgHasTrialsOnly || undefined,
    }),
    [debouncedOrgSearch, selectedOrgTypes, orgHasTrialsOnly]
  )

  const clearFilters = () => {
    trials.clear()
    news.clear()
    funding.clear()
    setOrgSearchText('')
    setSelectedOrgTypes([])
    setOrgHasTrialsOnly(false)
  }

  const toggleFilter = () => setFilterOpen(v => !v)

  const showFilterSidebar = filterOpen
    && activeTab !== 'trials'
    && activeTab !== 'merges'
    && activeTab !== 'funding'
    && activeTab !== 'news'

  return (
    <div className="app">
      <ProgressBar />
      <div className="stats-bar">
        <span className="app-title">AiCure Clinical Intelligence</span>
        {stats ? (
          <div className="stats-pills">
            <span className="stat-pill">{stats.total_trials.toLocaleString()} trials</span>
            <span className="stat-pill accent">{stats.trials_with_news} with news</span>
            <span className="stat-pill">{stats.total_news} news items</span>
            {stats.last_ingested && (
              <span className="stat-pill muted">
                Ingested {stats.last_ingested.slice(0, 10)}
              </span>
            )}
            {grantStats && grantStats.total_grants > 0 && (
              <>
                <span className="stat-pill">{grantStats.total_grants.toLocaleString()} grants</span>
                <span className="stat-pill accent">{grantStats.active_grants} active</span>
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
        {/* Views sidebar — Trials, News, and Funding tabs */}
        {activeTab === 'trials' && (
          <ViewsSidebar
            tab="trials"
            gridApiRef={trialApiRef}
            getCurrentConditions={trials.getCurrent}
            onApplyConditions={trials.setConditions}
            conditions={trials.conditions}
            gridStateBump={trialGridBump.value}
          />
        )}
        {activeTab === 'news' && (
          <ViewsSidebar
            tab="news"
            gridApiRef={newsApiRef}
            getCurrentConditions={news.getCurrent}
            onApplyConditions={news.setConditions}
            conditions={news.conditions}
            gridStateBump={newsGridBump.value}
          />
        )}
        {activeTab === 'funding' && (
          <ViewsSidebar
            tab="funding"
            gridApiRef={fundingApiRef}
            getCurrentConditions={funding.getCurrent}
            onApplyConditions={funding.setConditions}
            conditions={funding.conditions}
            gridStateBump={fundingGridBump.value}
          />
        )}

        <div className="app-body">
          {/* Orgs-only filter sidebar (news and funding moved to inline FilterBar) */}
          {showFilterSidebar && (
            <div className="filter-sidebar">
              <div className="filter-sidebar-inner">
                <div className="filter-group">
                  <div className="filter-label">Search</div>
                  <input
                    className="search-input"
                    type="text"
                    placeholder="Search…"
                    value={orgSearchText}
                    onChange={(e) => setOrgSearchText(e.target.value)}
                  />
                </div>

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
                conditions={trials.conditions}
                onAddCondition={trials.add}
                onEditCondition={trials.edit}
                onRemoveCondition={trials.remove}
                onClearConditions={trials.clear}
                therapeuticAreas={therapeuticAreas}
                countries={countries}
                onGridReady={(api) => { trialApiRef.current = api }}
                onGridStateChange={trialGridBump.bump}
              />
            )}
            {activeTab === 'news' && (
              <NewsTable
                filters={newsFilters}
                conditions={news.conditions}
                onAddCondition={news.add}
                onEditCondition={news.edit}
                onRemoveCondition={news.remove}
                onClearConditions={news.clear}
                onGridReady={(api) => { newsApiRef.current = api }}
                onGridStateChange={newsGridBump.bump}
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
              <FundingTable
                filters={fundingFilters}
                onSelectTrial={setOrgSelectedTrial}
                conditions={funding.conditions}
                onAddCondition={funding.add}
                onEditCondition={funding.edit}
                onRemoveCondition={funding.remove}
                onClearConditions={funding.clear}
                onGridReady={(api) => { fundingApiRef.current = api }}
                onGridStateChange={fundingGridBump.bump}
              />
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
            getMergeStats().then(r => setPendingMergeCount(r.data.pending)).catch(console.error)
          }}
        />
      )}
    </div>
  )
}
