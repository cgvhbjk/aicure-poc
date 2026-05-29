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

  // Trial filters — Airtable-style conditions
  const [conditions, setConditions] = useState([])

  // News conditions (Airtable-style, replaces old checkbox state)
  const [newsConditions, setNewsConditions] = useState([])

  // Funding conditions
  const [fundingConditions, setFundingConditions] = useState([])

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

  // Grid state bump counters (trigger ViewsSidebar auto-save)
  const [gridStateBump, setGridStateBump] = useState(0)
  const bumpGridState = useCallback(() => setGridStateBump(v => v + 1), [])
  const [newsGridStateBump, setNewsGridStateBump] = useState(0)
  const bumpNewsGridState = useCallback(() => setNewsGridStateBump(v => v + 1), [])
  const [fundingGridStateBump, setFundingGridStateBump] = useState(0)
  const bumpFundingGridState = useCallback(() => setFundingGridStateBump(v => v + 1), [])

  const debouncedOrgSearch = useDebounce(orgSearchText, 400)

  // Trials condition handlers
  const addCondition = useCallback((c) => setConditions(prev => [...prev, c]), [])
  const editCondition = useCallback((c) => setConditions(prev => prev.map(x => x.id === c.id ? c : x)), [])
  const removeCondition = useCallback((id) => setConditions(prev => prev.filter(x => x.id !== id)), [])
  const clearConditions = useCallback(() => setConditions([]), [])
  const getCurrentConditions = useCallback(() => conditions, [conditions])

  // News condition handlers
  const addNewsCondition = useCallback((c) => setNewsConditions(prev => [...prev, c]), [])
  const editNewsCondition = useCallback((c) => setNewsConditions(prev => prev.map(x => x.id === c.id ? c : x)), [])
  const removeNewsCondition = useCallback((id) => setNewsConditions(prev => prev.filter(x => x.id !== id)), [])
  const clearNewsConditions = useCallback(() => setNewsConditions([]), [])
  const getCurrentNewsConditions = useCallback(() => newsConditions, [newsConditions])

  // Funding condition handlers
  const addFundingCondition = useCallback((c) => setFundingConditions(prev => [...prev, c]), [])
  const editFundingCondition = useCallback((c) => setFundingConditions(prev => prev.map(x => x.id === c.id ? c : x)), [])
  const removeFundingCondition = useCallback((id) => setFundingConditions(prev => prev.filter(x => x.id !== id)), [])
  const clearFundingConditions = useCallback(() => setFundingConditions([]), [])
  const getCurrentFundingConditions = useCallback(() => fundingConditions, [fundingConditions])

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

  const { apiParams: newsFilters } = useMemo(
    () => compileNewsConditions(newsConditions),
    [newsConditions]
  )

  const { apiParams: fundingFilters } = useMemo(
    () => compileFundingConditions(fundingConditions),
    [fundingConditions]
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
    setConditions([])
    setNewsConditions([])
    setFundingConditions([])
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
            getCurrentConditions={getCurrentConditions}
            onApplyConditions={setConditions}
            conditions={conditions}
            gridStateBump={gridStateBump}
          />
        )}
        {activeTab === 'news' && (
          <ViewsSidebar
            tab="news"
            gridApiRef={newsApiRef}
            getCurrentConditions={getCurrentNewsConditions}
            onApplyConditions={setNewsConditions}
            conditions={newsConditions}
            gridStateBump={newsGridStateBump}
          />
        )}
        {activeTab === 'funding' && (
          <ViewsSidebar
            tab="funding"
            gridApiRef={fundingApiRef}
            getCurrentConditions={getCurrentFundingConditions}
            onApplyConditions={setFundingConditions}
            conditions={fundingConditions}
            gridStateBump={fundingGridStateBump}
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
                conditions={newsConditions}
                onAddCondition={addNewsCondition}
                onEditCondition={editNewsCondition}
                onRemoveCondition={removeNewsCondition}
                onClearConditions={clearNewsConditions}
                onGridReady={(api) => { newsApiRef.current = api }}
                onGridStateChange={bumpNewsGridState}
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
                conditions={fundingConditions}
                onAddCondition={addFundingCondition}
                onEditCondition={editFundingCondition}
                onRemoveCondition={removeFundingCondition}
                onClearConditions={clearFundingConditions}
                onGridReady={(api) => { fundingApiRef.current = api }}
                onGridStateChange={bumpFundingGridState}
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
            getMergeStats().then(r => setPendingMergeCount(r.data.pending)).catch(() => {})
          }}
        />
      )}
    </div>
  )
}
