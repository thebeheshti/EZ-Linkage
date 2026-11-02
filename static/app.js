/* Every state-changing request carries this header; the server rejects POSTs without it (CSRF guard). */
const nativeFetch = window.fetch.bind(window);
window.fetch = (url, options = {}) => {
  const method = String(options.method || 'GET').toUpperCase();
  if (method !== 'GET' && method !== 'HEAD') {
    const headers = new Headers(options.headers || {});
    headers.set('X-EZ-Linkage', '1');
    options = { ...options, headers };
  }
  return nativeFetch(url, options);
};

/* Dark mode */
const darkModeToggle = document.getElementById('darkModeToggle');
function applyDark(dark) {
  document.documentElement.classList.toggle('dark', dark);
  darkModeToggle.setAttribute('title', dark ? 'Switch to light mode' : 'Switch to dark mode');
  darkModeToggle.setAttribute('aria-label', dark ? 'Switch to light mode' : 'Switch to dark mode');
}
applyDark(localStorage.getItem('ezlinkage-theme') === 'dark');
darkModeToggle.addEventListener('click', () => {
  const isDark = document.documentElement.classList.contains('dark');
  localStorage.setItem('ezlinkage-theme', isDark ? 'light' : 'dark');
  applyDark(!isDark);
});

const form = document.getElementById('uploadForm');
const modeSelect = document.getElementById('modeSelect');
const superEzModeInput = document.getElementById('superEzModeInput');
const file1Input = document.querySelector('input[name="file1"]');
const file2Label = document.getElementById('file2Label');
const file2Input = document.querySelector('input[name="file2"]');
const stepper = document.getElementById('stepper');
const stepStatus = document.getElementById('stepStatus');
const uploadPanel = document.getElementById('uploadPanel');
const statusBox = document.getElementById('status');
const progressShell = document.getElementById('progressShell');
const progressBar = document.getElementById('progressBar');
const progressLabel = document.getElementById('progressLabel');
const workflowShell = document.getElementById('workflowShell');
const resultsShell = document.getElementById('resultsShell');
const resultsHead = document.getElementById('resultsHead');
const body = document.getElementById('resultsBody');
const searchBox = document.getElementById('searchBox');
const classFilter = document.getElementById('classFilter');
const highlightDiffToggle = document.getElementById('highlightDiffToggle');
const saveButton = document.getElementById('saveButton');
const columnsButton = document.getElementById('columnsButton');
const prevPageButton = document.getElementById('prevPageButton');
const nextPageButton = document.getElementById('nextPageButton');
const pageStatus = document.getElementById('pageStatus');
const exportMenuButton = document.getElementById('exportMenuButton');
const liveProgressHost = document.getElementById('liveProgressHost');
const stopJobButton = document.getElementById('stopJobButton');
const settingsMenuButton = document.getElementById('settingsMenuButton');
const exportModal = document.getElementById('exportModal');
const settingsModal = document.getElementById('settingsModal');
const previewModal = document.getElementById('previewModal');
const previewContent = document.getElementById('previewContent');
const previewHelp = document.getElementById('previewHelp');
const previewMappingEditor = document.getElementById('previewMappingEditor');
const previewMappingRows = document.getElementById('previewMappingRows');
const addPreviewMappingButton = document.getElementById('addPreviewMappingButton');
const mappingFile1Select = document.getElementById('mappingFile1Select');
const mappingFile2Select = document.getElementById('mappingFile2Select');
const mappingNameInput = document.getElementById('mappingNameInput');
const previewLinkageActions = document.getElementById('previewLinkageActions');
const previewDedupeActions = document.getElementById('previewDedupeActions');
const dedupeAddFieldSelect = document.getElementById('dedupeAddFieldSelect');
const dedupeAddFieldButton = document.getElementById('dedupeAddFieldButton');
const columnsModal = document.getElementById('columnsModal');
const comparatorModal = document.getElementById('comparatorModal');
const comparatorOptions = document.getElementById('comparatorOptions');
const blockingModal = document.getElementById('blockingModal');
const columnsOptions = document.getElementById('columnsOptions');
const columnsHint = document.getElementById('columnsHint');
const addColumnsPairButton = document.getElementById('addColumnsPairButton');
const blockingOptions = document.getElementById('blockingOptions');
const cancelPreviewButton = document.getElementById('cancelPreviewButton');
const continuePreviewButton = document.getElementById('continuePreviewButton');
const cancelColumnsButton = document.getElementById('cancelColumnsButton');
const applyColumnsButton = document.getElementById('applyColumnsButton');
const backToBlockingButton = document.getElementById('backToBlockingButton');
const confirmComparatorButton = document.getElementById('confirmComparatorButton');
const backToVariablesButton = document.getElementById('backToVariablesButton');
const regenerateBlockingButton = document.getElementById('regenerateBlockingButton');
const addBlockingRuleButton = document.getElementById('addBlockingRuleButton');
const runWithBlockingButton = document.getElementById('runWithBlockingButton');
const exportFile2Choice = document.getElementById('exportFile2Choice');
const cancelExportButton = document.getElementById('cancelExportButton');
const runExportButton = document.getElementById('runExportButton');
const apiUrlInput = document.getElementById('apiUrlInput');
const apiModelInput = document.getElementById('apiModelInput');
const apiKeyInput = document.getElementById('apiKeyInput');
const removeApiKeyButton = document.getElementById('removeApiKeyButton');
const apiKeySourceInputs = document.querySelectorAll('input[name="apiKeySource"]');
const apiKeyTypedBlock = document.getElementById('apiKeyTypedBlock');
const apiKeyVariableBlock = document.getElementById('apiKeyVariableBlock');
const apiKeyVariableInput = document.getElementById('apiKeyVariableInput');
const apiKeyVariableStatus = document.getElementById('apiKeyVariableStatus');
const temperatureInput = document.getElementById('temperatureInput');
const topPInput = document.getElementById('topPInput');
const topKInput = document.getElementById('topKInput');
const extraParamsInput = document.getElementById('extraParamsInput');
const reasoningEffortInput = document.getElementById('reasoningEffortInput');
const matchThresholdInput = document.getElementById('matchThresholdInput');
const nonMatchThresholdInput = document.getElementById('nonMatchThresholdInput');
const matchThresholdValue = document.getElementById('matchThresholdValue');
const nonMatchThresholdValue = document.getElementById('nonMatchThresholdValue');
const blockingRatioMinInput = document.getElementById('blockingRatioMinInput');
const blockingRatioMaxInput = document.getElementById('blockingRatioMaxInput');
const uncertainRangeValue = document.getElementById('uncertainRangeValue');
const thresholdBand = document.getElementById('thresholdBand');
const settingsTabs = Array.from(document.querySelectorAll('.settings-tab'));
const settingsPanels = Array.from(document.querySelectorAll('.settings-panel'));
const cancelSettingsButton = document.getElementById('cancelSettingsButton');
const saveSettingsButton = document.getElementById('saveSettingsButton');
const settingsError = document.getElementById('settingsError');
[previewModal, blockingModal, comparatorModal].forEach(panel => workflowShell.appendChild(panel));
let currentJob = null;
let currentSchemaMappingJob = null;
let currentBlockingJob = null;
let currentComparatorJob = null;
let lastSchemaMappingJob = null;
let results = [];
let currentPage = 1;
let totalPages = 1;
let totalFilteredResults = 0;
let totalCandidatePairs = 0;
const pageSize = 1000;
let progressTimer = null;
let progressHideTimer = null;
let _progressPct = 0;
let pendingFormData = null;
let pendingSchemaMapping = null;
let pendingSelectedFields = [];
let pendingBlockingPlan = null;
let pendingComparisonPlan = null;
let blockingRuleRowCounter = 0;
let pendingPreviewRows1 = [];
let pendingPreviewRows2 = [];
let pendingManualMappings = [];
let pendingApprovedBlockingRules = [];
let workflowStopRequested = false;
let activeAbortController = null;
let comparatorNeedsRefresh = false;
let linkageFields = [];
let extraVisibleResultColumns = [];
let availableResultFields = { file1: [], file2: [] };
let availableFieldLabels = {};
let availableFieldMeta = {};
let currentWizardStep = 'upload';
let statusResetTimer = null;
let lastResultsStatusParts = [];
// Status-strip pieces for the Results page: file sizes and pairs compared, the
// three status counts (kept current as labels change) and any run warnings.
let resultsSummaryParts = [];
let resultsCounts = { Match: 0, Uncertain: 0, 'Non-Match': 0 };
let resultsWarnings = [];
function activateSettingsTab(tabName = 'api') {
  settingsTabs.forEach(tab => {
    const active = tab.dataset.tab === tabName;
    tab.classList.toggle('active', active);
    tab.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  settingsPanels.forEach(panel => {
    panel.classList.toggle('active', panel.dataset.panel === tabName);
  });
}

function syncThresholdWidgets() {
  const matchValue = Math.max(0, Math.min(1, Number(matchThresholdInput.value || 0.95)));
  const nonMatchValue = Math.max(0, Math.min(1, Number(nonMatchThresholdInput.value || 0.65)));
  matchThresholdInput.value = matchValue.toFixed(2);
  nonMatchThresholdInput.value = nonMatchValue.toFixed(2);
  if (matchThresholdValue) matchThresholdValue.textContent = matchValue.toFixed(2);
  if (nonMatchThresholdValue) nonMatchThresholdValue.textContent = nonMatchValue.toFixed(2);
  const zoneNonMatchRange = document.getElementById('zoneNonMatchRange');
  const zoneMatchRange    = document.getElementById('zoneMatchRange');
  if (zoneNonMatchRange) zoneNonMatchRange.textContent = `0.00 – ${nonMatchValue.toFixed(2)}`;
  if (uncertainRangeValue) uncertainRangeValue.textContent = `${nonMatchValue.toFixed(2)} – ${matchValue.toFixed(2)}`;
  if (zoneMatchRange) zoneMatchRange.textContent = `${matchValue.toFixed(2)} – 1.00`;
  if (thresholdBand) {
    const uncertainWidth = Math.max(0, matchValue - nonMatchValue);
    thresholdBand.style.setProperty('--nonmatch-band', `${(nonMatchValue * 100).toFixed(1)}%`);
    thresholdBand.style.setProperty('--uncertain-band', `${(uncertainWidth * 100).toFixed(1)}%`);
    thresholdBand.style.setProperty('--match-band', `${(Math.max(0, 1 - matchValue) * 100).toFixed(1)}%`);
  }
}

// The AI service settings live only in settings.json; earlier versions also kept them here.
for (const key of ['apiBaseUrl', 'apiModel', 'temperature', 'topP', 'topK', 'reasoningEnabled', 'reasoningEffort']) {
  try { localStorage.removeItem(key); } catch (error) { /* nothing stored */ }
}

function optionalSetting(value) {
  return value === null || value === undefined ? '' : String(value);
}

function loadSettings() {
  // Fetch backend defaults first, then layer localStorage and settings.json on top
  fetch('/api/defaults').then(r => r.ok ? r.json() : {}).then(defaults => {
    apiKeyInput.value = '';
    matchThresholdInput.value = localStorage.getItem('matchThreshold') || String(defaults.matchThreshold ?? 0.95);
    nonMatchThresholdInput.value = localStorage.getItem('nonMatchThreshold') || String(defaults.nonMatchThreshold ?? 0.65);
    if (blockingRatioMinInput) blockingRatioMinInput.value = localStorage.getItem('blockingRatioMinPct') || String((defaults.blockingRatioMin ?? 0.0002) * 100);
    if (blockingRatioMaxInput) blockingRatioMaxInput.value = localStorage.getItem('blockingRatioMaxPct') || String((defaults.blockingRatioMax ?? 0.002) * 100);
    syncThresholdWidgets();
    // Then override from server-side settings.json (persists across portable zip moves)
    fetch('/api/settings').then(r => r.ok ? r.json() : {}).then(data => {
      apiUrlInput.value = data.apiBaseUrl || '';
      apiModelInput.value = data.apiModel || '';
      void migrateBrowserApiKey(!!data.apiKeySaved).then(showApiKeyState);
      apiKeyVariableInput.value = data.apiKeyVariable || '';
      showApiKeySource(data.apiKeySource === 'variable' ? 'variable' : 'typed');
      showApiKeyVariableStatus(apiKeyVariableInput.value.trim(), !!data.apiKeyVariableFound);
      if (data.matchThreshold) matchThresholdInput.value = data.matchThreshold;
      if (data.nonMatchThreshold) nonMatchThresholdInput.value = data.nonMatchThreshold;
      if (data.blockingRatioMin !== undefined && blockingRatioMinInput) blockingRatioMinInput.value = String(Number(data.blockingRatioMin) * 100);
      if (data.blockingRatioMax !== undefined && blockingRatioMaxInput) blockingRatioMaxInput.value = String(Number(data.blockingRatioMax) * 100);
      temperatureInput.value = optionalSetting(data.temperature);
      topPInput.value = optionalSetting(data.topP);
      topKInput.value = optionalSetting(data.topK);
      reasoningEffortInput.value = optionalSetting(data.reasoningEffort);
      extraParamsInput.value = data.extraParams && Object.keys(data.extraParams).length
        ? JSON.stringify(data.extraParams, null, 2) : '';
      syncThresholdWidgets();
    }).catch(() => {});
  }).catch(() => {});
}

function saveSettings() {
  const matchValue = Number(matchThresholdInput.value || 0.95);
  const nonMatchValue = Number(nonMatchThresholdInput.value || 0.65);
  if (!Number.isFinite(matchValue) || !Number.isFinite(nonMatchValue) || matchValue < 0 || matchValue > 1 || nonMatchValue < 0 || nonMatchValue > 1) {
    throw new Error('Thresholds must be numbers between 0 and 1.');
  }
  if (nonMatchValue >= matchValue) {
    throw new Error('Non-Match Threshold must be lower than Match Threshold.');
  }
  // An empty field means "Model default": nothing is sent for it.
  const optionalNumber = (input, label, min, max) => {
    const text = input.value.trim();
    if (!text) return null;
    const value = Number(text);
    if (!Number.isFinite(value) || value < min || value > max) {
      throw new Error(`${label} must be a number from ${min} to ${max}, or empty for the model's default.`);
    }
    return value;
  };
  const temperature = optionalNumber(temperatureInput, 'Temperature', 0, 2);
  const topP = optionalNumber(topPInput, 'Top-P', 0, 1);
  const topK = optionalNumber(topKInput, 'Top-K', 1, 100000);
  if (topK !== null && !Number.isInteger(topK)) throw new Error('Top-K must be a whole number, or empty for the model\'s default.');
  let extraParams = null;
  if (extraParamsInput.value.trim()) {
    try { extraParams = JSON.parse(extraParamsInput.value); } catch (error) { extraParams = undefined; }
    if (!extraParams || typeof extraParams !== 'object' || Array.isArray(extraParams)) {
      throw new Error('Extra request parameters must be a JSON object, like {"min_p": 0.05}, or empty.');
    }
  }
  const blockingRatioMinPct = Number(blockingRatioMinInput ? blockingRatioMinInput.value : 0.02) || 0.02;
  const blockingRatioMaxPct = Number(blockingRatioMaxInput ? blockingRatioMaxInput.value : 0.2) || 0.2;
  const apiKeySource = selectedApiKeySource();
  const apiKeyVariable = apiKeyVariableInput.value.trim();
  if (apiKeySource === 'variable' && !VARIABLE_NAME.test(apiKeyVariable)) {
    throw new Error('Enter the name of the system variable that holds the API key, for example MY_API_KEY.');
  }
  const blockingRatioMin = blockingRatioMinPct / 100;
  const blockingRatioMax = blockingRatioMaxPct / 100;
  const payload = {
    apiBaseUrl: apiUrlInput.value.trim().replace(/\/+$/, ''),
    apiModel: apiModelInput.value.trim(),
    apiKeySource,
    apiKeyVariable,
    matchThreshold: String(matchValue),
    nonMatchThreshold: String(nonMatchValue),
    blockingRatioMin,
    blockingRatioMax,
    temperature,
    topP,
    topK,
    reasoningEffort: reasoningEffortInput.value.trim() || null,
    extraParams,
  };
  localStorage.setItem('matchThreshold', payload.matchThreshold);
  localStorage.setItem('nonMatchThreshold', payload.nonMatchThreshold);
  localStorage.setItem('blockingRatioMinPct', String(blockingRatioMinPct));
  localStorage.setItem('blockingRatioMaxPct', String(blockingRatioMaxPct));
  fetch('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }).catch(() => {});
  if (apiKeySource === 'typed' && apiKeyInput.value) {
    // The key goes to the server's .env and is never kept or shown in the browser.
    void saveApiKey(apiKeyInput.value);
    apiKeyInput.value = '';
  }
  syncThresholdWidgets();
}

async function saveApiKey(key) {
  const response = await fetch('/api/api_key', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ key }) });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || 'Could not save the API key');
  showApiKeyState(!!data.apiKeySaved);
  return !!data.apiKeySaved;
}

function showApiKeyState(saved) {
  apiKeyInput.placeholder = saved
    ? 'Key saved. Type a new key to replace it.'
    : 'Leave blank for unauthenticated local endpoints';
  if (removeApiKeyButton) removeApiKeyButton.style.display = saved ? '' : 'none';
}

const VARIABLE_NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;
let apiKeyVariableTimer = null;

function selectedApiKeySource() {
  return Array.from(apiKeySourceInputs).find(input => input.checked)?.value === 'variable' ? 'variable' : 'typed';
}

function showApiKeySource(source) {
  apiKeySourceInputs.forEach(input => { input.checked = input.value === source; });
  apiKeyTypedBlock.hidden = source !== 'typed';
  apiKeyVariableBlock.hidden = source !== 'variable';
}

function showApiKeyVariableStatus(name, found) {
  let text = 'The name of an environment variable on this computer that holds the key.';
  let state = '';
  if (name && !VARIABLE_NAME.test(name)) {
    text = 'Use only letters, digits and underscores, for example MY_API_KEY.';
    state = 'is-missing';
  } else if (name && found) {
    text = `Found ${name} on this computer. Its value is never shown.`;
    state = 'is-found';
  } else if (name) {
    text = `${name} is not set on this computer. If you just created it, restart EZ Linkage.`;
    state = 'is-missing';
  }
  apiKeyVariableStatus.textContent = text;
  apiKeyVariableStatus.classList.remove('is-found', 'is-missing');
  if (state) apiKeyVariableStatus.classList.add(state);
}

async function checkApiKeyVariable() {
  const name = apiKeyVariableInput.value.trim();
  if (!name || !VARIABLE_NAME.test(name)) {
    showApiKeyVariableStatus(name, false);
    return;
  }
  try {
    const response = await fetch(`/api/api_key_variable?name=${encodeURIComponent(name)}`);
    const data = await response.json();
    if (apiKeyVariableInput.value.trim() === name) showApiKeyVariableStatus(name, !!data.found);
  } catch (error) { /* keep the previous message */ }
}

apiKeySourceInputs.forEach(input => input.addEventListener('change', () => {
  showApiKeySource(selectedApiKeySource());
  if (selectedApiKeySource() === 'variable') apiKeyVariableInput.focus();
}));
apiKeyVariableInput.addEventListener('input', () => {
  clearTimeout(apiKeyVariableTimer);
  apiKeyVariableTimer = setTimeout(checkApiKeyVariable, 300);
});

async function migrateBrowserApiKey(serverHasKey) {
  // Earlier versions kept the key in localStorage; hand it to the server once, then forget it.
  const legacyKey = localStorage.getItem('aiApiKey');
  localStorage.removeItem('aiApiKey');
  if (legacyKey && !serverHasKey) {
    try { return await saveApiKey(legacyKey); } catch (error) { return false; }
  }
  return serverHasKey;
}

// Agent requests may be refused until the user approves sending data to a remote AI endpoint.
async function postAgentJob(url, formData) {
  if (!formData.get('api_url') || !formData.get('api_model')) {
    activateSettingsTab('api');
    loadSettings();
    settingsError.hidden = true;
    settingsModal.classList.add('open');
    throw new Error("Enter your AI service's Base URL and model name in Settings first.");
  }
  let response = await cancelableFetch(url, { method: 'POST', body: formData });
  let data = await response.json();
  if (response.status === 409 && data.needs_endpoint_approval) {
    const approved = window.confirm(
      `The AI service at ${data.endpoint} is not on this computer or a private network.\n\n` +
      'EZ Linkage sends it sample records from your files, which can include names, dates of birth and ID numbers.\n\n' +
      'Send data to this service? You will only be asked once for this address.'
    );
    if (!approved) throw new Error(`Stopped: sending data to ${data.endpoint} was not approved.`);
    const approval = await fetch('/api/approve_endpoint', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ endpoint: data.endpoint }),
    });
    if (!approval.ok) throw new Error('Could not record the endpoint approval.');
    response = await cancelableFetch(url, { method: 'POST', body: formData });
    data = await response.json();
  }
  return { response, data };
}

function normalizeApiBaseUrl(url) {
  return String(url || '').trim().replace(/\/chat\/completions\/?$/, '').replace(/\/+$/, '');
}

function humanizePreviewKey(key) {
  if (key === '__source_id') return 'Source ID';
  return fieldLabel(key);
}

function previewFieldMappingsBySource() {
  const file1 = {};
  const file2 = {};
  const mode = (pendingSchemaMapping || {}).mode || modeSelect.value;
  const suggestedSet = new Set((pendingSchemaMapping || {}).suggested_fields || []);
  (((pendingSchemaMapping || {}).available_fields) || []).forEach(option => {
    if (mode === 'dedupe') {
      if (option.field && suggestedSet.has(option.field)) file1[option.field] = option;
    } else {
      if (option.file1_field) file1[option.file1_field] = option;
      if (option.file2_field) file2[option.file2_field] = option;
    }
  });
  return { file1, file2 };
}

function previewHeaderHtml(column, sourceKey) {
  const label = humanizePreviewKey(column);
  const sourceMappings = previewFieldMappingsBySource()[sourceKey] || {};
  const match = sourceMappings[column];
  if (!match) {
    return `<div class="preview-header-cell"><span>${escapeHtml(label)}</span></div>`;
  }
  const mode = (pendingSchemaMapping || {}).mode || modeSelect.value;
  const badgeLabel = 'Used for matching';
  const sharedLabel = fieldLabel(match.field);
  const note = sharedLabel !== label && mode !== 'dedupe' ? `<span class="preview-header-note">Standardized as ${escapeHtml(sharedLabel)}</span>` : '';
  return `
    <div class="preview-header-cell">
      <span>${escapeHtml(label)}</span>
      <span class="link-badge">
        <span class="link-badge-label">${badgeLabel}</span>
        <button class="link-badge-remove" data-remove-field="${escapeHtml(match.field)}" type="button" title="Stop using ${escapeHtml(sharedLabel)} for matching" aria-label="Stop using ${escapeHtml(sharedLabel)} for matching">
          <svg width="8" height="8" viewBox="0 0 8 8" fill="none" aria-hidden="true">
            <path d="M1 1 7 7M7 1 1 7" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"/>
          </svg>
        </button>
      </span>
      ${note}
    </div>
  `;
}

function previewHelpText() {
  return 'Check that each field was matched to the right field in the other file. Fields marked "Used for matching" are compared in the next steps; select × to leave one out, or add a pairing below if one was missed. Mistakes here carry through to the results.';
}

function previewTableHtml(rows, title, sourceKey) {
  if (!rows || !rows.length) {
    return `
      <section class="preview-panel">
        <h3>${escapeHtml(title)}</h3>
        <div class="preview-table-wrap"><div class="preview-empty">No records to preview.</div></div>
      </section>
    `;
  }
  const sourceMappings = previewFieldMappingsBySource()[sourceKey] || {};
  const columns = Object.keys(rows[0] || {}).sort((a, b) => {
    const aLinked = !!sourceMappings[a];
    const bLinked = !!sourceMappings[b];
    if (aLinked && !bLinked) return -1;
    if (!aLinked && bLinked) return 1;
    return 0;
  });
  return `
    <section class="preview-panel">
      <h3>${escapeHtml(title)}</h3>
      <div class="preview-table-wrap">
        <table class="preview-table">
          <thead>
            <tr>${columns.map(column => `<th>${previewHeaderHtml(column, sourceKey)}</th>`).join('')}</tr>
          </thead>
          <tbody>
            ${rows.map(row => `
              <tr>
                ${columns.map(column => `<td>${escapeHtml(row[column] ?? '')}</td>`).join('')}
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    </section>
  `;
}

function standardizedFieldOptions(fields, selectedValue = '') {
  return (Array.isArray(fields) ? fields : []).map(field => `
    <option value="${escapeHtml(field)}" ${field === selectedValue ? 'selected' : ''}>${escapeHtml(fieldLabel(field))}</option>
  `).join('');
}

function renderPreviewMappingEditor(schemaMapping) {
  const mode = schemaMapping.mode || modeSelect.value;
  previewMappingEditor.style.display = 'grid';
  if (mode !== 'linkage') {
    previewMappingRows.style.display = 'none';
    previewLinkageActions.style.display = 'none';
    previewDedupeActions.style.display = 'flex';
    pendingManualMappings = [];
    const suggestedSet = new Set(schemaMapping.suggested_fields || []);
    const unselected = (schemaMapping.available_fields || []).filter(opt => !suggestedSet.has(opt.field));
    dedupeAddFieldSelect.innerHTML = unselected.length
      ? `<option value="">Choose a variable to add...</option>` +
        unselected.map(opt => `<option value="${escapeHtml(opt.field)}">${escapeHtml(opt.label || fieldLabel(opt.field))}</option>`).join('')
      : `<option value="">All available variables are selected</option>`;
    dedupeAddFieldSelect.disabled = !unselected.length;
    dedupeAddFieldButton.disabled = !unselected.length;
    return;
  }
  previewMappingRows.style.display = 'none';
  previewLinkageActions.style.display = '';
  previewDedupeActions.style.display = 'none';
  mappingFile1Select.innerHTML = `<option value="">Choose a File 1 field</option>` +
    standardizedFieldOptions(schemaMapping.standardized_fields1 || []);
  mappingFile2Select.innerHTML = `<option value="">Choose a File 2 field</option>` +
    standardizedFieldOptions(schemaMapping.standardized_fields2 || []);
}

function buildManualFieldOption(sharedName, sharedSpec) {
  const profile1 = (((pendingSchemaMapping || {}).standardized_profiles || {}).file1 || {})[sharedSpec.file1_field] || {};
  const profile2 = (((pendingSchemaMapping || {}).standardized_profiles || {}).file2 || {})[sharedSpec.file2_field] || {};
  return {
    field: sharedName,
    label: sharedSpec.label || fieldLabel(sharedName),
    suggested: true,
    reason: sharedSpec.reason || 'Manual mapping added by user.',
    significance: '',
    is_canonical: false,
    role: sharedSpec.role || 'supporting',
    file1_field: sharedSpec.file1_field,
    file2_field: sharedSpec.file2_field,
    profile1,
    profile2,
  };
}

function applyManualMappingsToSchemaMapping() {
  if (!pendingSchemaMapping || (pendingSchemaMapping.mode || modeSelect.value) !== 'linkage') return;
  const manualMappings = pendingManualMappings;
  const sharedPlan = JSON.parse(JSON.stringify(pendingSchemaMapping.shared_field_plan || { shared_fields: {}, standardized_fields: [], notes: [] }));
  if (!sharedPlan.shared_fields) sharedPlan.shared_fields = {};
  if (!Array.isArray(sharedPlan.standardized_fields)) sharedPlan.standardized_fields = [];
  const available = [...(pendingSchemaMapping.available_fields || [])];

  const seenPairs = new Set();
  for (const mapping of manualMappings) {
    const sharedName = mapping.shared_name;
    const pairKey = `${mapping.file1_field}||${mapping.file2_field}`;

    // Skip duplicate manual mappings (same column pair appeared earlier in the list)
    if (seenPairs.has(pairKey)) continue;
    seenPairs.add(pairKey);

    const sharedSpec = {
      label: humanizeFieldName(sharedName),
      suggested: true,
      reason: 'Manual mapping added by user.',
      role: 'supporting',
      file1_field: mapping.file1_field,
      file2_field: mapping.file2_field,
      file1_alternates: [],
      file2_alternates: [],
    };

    // Remove any existing entry that maps the same column pair under a different name
    for (let i = available.length - 1; i >= 0; i--) {
      const opt = available[i];
      if (opt.field !== sharedName && opt.file1_field === mapping.file1_field && opt.file2_field === mapping.file2_field) {
        sharedPlan.standardized_fields = sharedPlan.standardized_fields.filter(f => f !== opt.field);
        delete sharedPlan.shared_fields[opt.field];
        available.splice(i, 1);
      }
    }

    sharedPlan.shared_fields[sharedName] = sharedSpec;
    if (!sharedPlan.standardized_fields.includes(sharedName)) {
      sharedPlan.standardized_fields.push(sharedName);
    }
    const existingIndex = available.findIndex(option => option.field === sharedName);
    const option = buildManualFieldOption(sharedName, sharedSpec);
    if (existingIndex >= 0) available.splice(existingIndex, 1, option);
    else available.push(option);
  }

  available.sort((left, right) => {
    return String(left.label || left.field).localeCompare(String(right.label || right.field));
  });

  pendingSchemaMapping.shared_field_plan = sharedPlan;
  pendingSchemaMapping.available_fields = available;
  pendingSchemaMapping.suggested_fields = Array.from(new Set([...(pendingSchemaMapping.suggested_fields || []), ...manualMappings.map(item => item.shared_name)]));
  availableFieldLabels = Object.fromEntries(available.map(option => [option.field, option.label || option.field]));
  availableFieldMeta = Object.fromEntries(available.map(option => [option.field, {
    role: option.role || '',
    profile1: option.profile1 || (((pendingSchemaMapping.field_profiles || {}).file1 || {})[option.field] || {}),
    profile2: option.profile2 || (((pendingSchemaMapping.field_profiles || {}).file2 || {})[option.field] || {})
  }]));
}

function _refreshPreviewTable(schemaMapping) {
  const sections = schemaMapping.preview_sections || {};
  const mode = schemaMapping.mode || modeSelect.value;
  if (previewHelp) {
    if (mode === 'linkage') {
      previewHelp.textContent = previewHelpText();
    } else {
      previewHelp.textContent = 'Check the fields chosen for finding duplicates. Fields marked "Used for matching" are compared in the next steps; select × to leave one out, or add another field below.';
    }
  }
  const sectionHtml = ['primary'].map(key => {
    const section = sections[key];
    if (!section) return '';
    const rows1 = Array.isArray(section.rows1) ? section.rows1 : [];
    const rows2 = Array.isArray(section.rows2) ? section.rows2 : [];
    if (!rows1.length && !rows2.length) return '';
    const panels = [previewTableHtml(rows1, 'File 1', 'file1')];
    if (mode === 'linkage') panels.push(previewTableHtml(rows2, 'File 2', 'file2'));
    return `<section class="preview-group"><div class="preview-group-grid">${panels.join('')}</div></section>`;
  }).join('');
  previewContent.innerHTML = sectionHtml || previewTableHtml(Array.isArray(schemaMapping.preview_rows1) ? schemaMapping.preview_rows1.slice(0, 5) : [], 'File 1', 'file1');
}

function renderPreviewWarnings(schemaMapping) {
  const box = document.getElementById('previewWarnings');
  if (!box) return;
  const warnings = Array.isArray(schemaMapping.selection_warnings) ? schemaMapping.selection_warnings : [];
  box.hidden = !warnings.length;
  box.innerHTML = warnings.length
    ? `<strong>Check before continuing</strong><ul>${warnings.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul>`
    : '';
}

function renderPreviewContent(schemaMapping) {
  renderPreviewWarnings(schemaMapping);
  _refreshPreviewTable(schemaMapping);
  renderPreviewMappingEditor(schemaMapping);
}

function formatPercent(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '';
  return `${(numeric * 100).toFixed(0)}%`;
}

function fieldMetaSummary(field) {
  const meta = availableFieldMeta[field] || {};
  const profile1 = meta.profile1 || {};
  const profile2 = meta.profile2 || {};
  const describe = profile => [
    profile.non_missing_rate !== undefined ? `${formatPercent(profile.non_missing_rate)} filled` : '',
    profile.uniqueness_rate !== undefined ? `${formatPercent(profile.uniqueness_rate)} unique` : '',
  ].filter(Boolean).join(', ');
  const parts = [];
  if (meta.role) parts.push(meta.role === 'primary' ? 'Identifying field' : 'Supporting field');
  const file1 = describe(profile1);
  const file2 = describe(profile2);
  if (file1 && file2) parts.push(`File 1: ${file1}`, `File 2: ${file2}`);
  else if (file1) parts.push(file1);
  return parts.join(' · ');
}

function setWorkflowBusy(isBusy) {
  document.body.classList.toggle('busy', isBusy);
  settingsMenuButton.disabled = isBusy;
  exportMenuButton.disabled = isBusy || !currentJob;
  stopJobButton.style.display = isBusy ? 'inline-flex' : 'none';
  stopJobButton.disabled = !isBusy;
  Array.from(form.elements).forEach(element => {
    if (element && element !== stopJobButton) element.disabled = isBusy;
  });
  searchBox.disabled = isBusy || !currentJob;
  classFilter.disabled = isBusy || !currentJob;
  highlightDiffToggle.disabled = isBusy || !currentJob;
  saveButton.disabled = isBusy || !currentJob;
  columnsButton.disabled = isBusy || !currentJob;
  prevPageButton.disabled = isBusy || !currentJob || currentPage <= 1;
  nextPageButton.disabled = isBusy || !currentJob || currentPage >= totalPages || totalFilteredResults === 0;
  setWizardStep(currentWizardStep);
}

const KNOWN_ACRONYMS = new Set(['ssn', 'dob', 'id', 'zip', 'icd', 'mrn', 'nhs', 'npi', 'ein', 'uid']);

function humanizeFieldName(name) {
  const words = String(name || '')
    .replace(/_/g, ' ')
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/\s+/g, ' ')
    .trim()
    .split(' ')
    .filter(Boolean)
    .map(word => (/^[A-Z0-9]{2,4}$/.test(word) || KNOWN_ACRONYMS.has(word.toLowerCase()) ? word.toUpperCase() : word.toLowerCase()));
  const text = words.join(' ');
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function fieldLabel(field) {
  // A label identical to the raw field name (e.g. "first_name") is no label at all.
  const label = availableFieldLabels[field];
  return (label && label !== field ? label : humanizeFieldName(field)) || field;
}

function updateModeInputs() {
  const isDedupe = modeSelect.value === 'dedupe';
  file2Label.style.display = isDedupe ? 'none' : 'grid';
  exportFile2Choice.style.display = isDedupe ? 'none' : 'flex';
  if (isDedupe && selectedExportType() === 'file2') {
    document.querySelector('input[name="exportType"][value="file1"]').checked = true;
  }
  file2Input.required = !isDedupe;
  if (isDedupe) {
    file2Input.value = '';
    updateFilePickerName(file2Input);
  }
}

function updateFilePickerName(input) {
  if (!(input instanceof HTMLInputElement)) return;
  const label = input.closest('.upload-file');
  const nameTarget = label?.querySelector('.file-picker-name');
  if (!nameTarget) return;
  nameTarget.textContent = input.files && input.files.length ? input.files[0].name : 'No file chosen';
}

function startProgress() {
  if (progressHideTimer) {
    clearTimeout(progressHideTimer);
    progressHideTimer = null;
  }
  _progressPct = 0;
  progressShell.classList.add('active');
  progressBar.classList.remove('indeterminate');
  updateProgress(3, 'Uploading files...');
}

function resetProgress() {
  _progressPct = 0;
}

function finishProgress(message = 'Linkage complete.') {
  clearInterval(progressTimer);
  progressTimer = null;
  progressBar.classList.remove('indeterminate');
  progressBar.style.width = '100%';
  progressLabel.textContent = message;
  if (progressHideTimer) clearTimeout(progressHideTimer);
  progressHideTimer = setTimeout(() => {
    progressShell.classList.remove('active');
    progressBar.style.width = '0%';
    progressHideTimer = null;
  }, 1400);
}

function failProgress(message = 'Linkage failed.') {
  clearInterval(progressTimer);
  progressTimer = null;
  progressBar.classList.remove('indeterminate');
  progressBar.style.width = '100%';
  progressLabel.textContent = cleanProgressMessage(message);
}

function cleanProgressMessage(message) {
  return String(message || 'Working...')
    .replace(/^(?:file\s*[12]|agent\s*\d+|file\s*[12]\s*agent|shared-field\s*agent|blocking\s*agent|matching\s*agent)\s*:\s*/i, '')
    .replace(/^AI agent is\s+/i, '')
    .replace(/^Super EZ Mode:\s*/i, '')
    .trim() || 'Working...';
}

function updateProgress(percent, message) {
  const clamped = Math.max(0, Math.min(100, Number(percent) || 0));
  if (clamped < _progressPct) return;
  _progressPct = clamped;
  progressBar.classList.remove('indeterminate');
  progressBar.style.width = `${clamped}%`;
  progressLabel.textContent = cleanProgressMessage(message);
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function pollJob(jobId) {
  while (true) {
    if (workflowStopRequested) throw new Error('Linkage canceled');
    const progressResponse = await cancelableFetch(`/api/progress?job_id=${encodeURIComponent(jobId)}`, { cache: 'no-store' });
    const progress = await progressResponse.json();
    if (!progressResponse.ok) throw new Error(progress.error || 'Could not read job progress');
    updateProgress(progress.progress, progress.message);
    if (progress.status === 'canceled') {
      throw new Error('Linkage canceled');
    }
    if (progress.status === 'error') throw new Error(progress.error || 'Linkage failed');
    if (progress.status === 'done') return progress;
    await sleep(900);
  }
}

async function cancelCurrentJob() {
  workflowStopRequested = true;
  if (activeAbortController) {
    activeAbortController.abort();
    activeAbortController = null;
  }
  stopJobButton.disabled = true;
  setWorkflowBusy(false);
  failProgress('Linkage canceled.');
  setStatus(['Linkage canceled']);
  try {
    const jobsToCancel = [currentJob, currentSchemaMappingJob, currentBlockingJob, currentComparatorJob].filter(Boolean);
    await Promise.all(jobsToCancel.map(job_id =>
      fetch('/api/cancel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_id })
      })
    ));
  } catch (error) {
    failProgress(error.message);
  }
}

async function fetchResultsPage(page = currentPage) {
  if (!currentJob) return;
  currentPage = page;
  if (currentWizardStep !== 'results') setResultsVisible(true);
  body.innerHTML = `<tr><td colspan="${resultColumnCount()}" class="empty">Loading...</td></tr>`;
  const params = new URLSearchParams({
    job_id: currentJob,
    page: String(currentPage),
    page_size: String(pageSize),
    q: searchBox.value.trim(),
    class: classFilter.value
  });
  const resultsResponse = await fetch(`/api/results_page?${params.toString()}`, { cache: 'no-store' });
  const data = await resultsResponse.json();
  if (!resultsResponse.ok) throw new Error(data.error || 'Could not load results');
  results = data.results;
  currentPage = data.page;
  totalPages = data.total_pages;
  totalFilteredResults = data.total_results;
  if (data.blocking_pairs !== undefined) totalCandidatePairs = data.blocking_pairs;
  if ((!linkageFields.length) && Array.isArray(data.selected_fields)) {
    const order = Array.isArray(data.display_order) ? data.display_order : [];
    linkageFields = order.length
      ? [...data.selected_fields].sort((a, b) => {
          const ai = order.indexOf(a), bi = order.indexOf(b);
          return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi);
        })
      : data.selected_fields;
  }
  const n = value => Number(value || 0).toLocaleString();
  resultsSummaryParts = [
    ...(data.mode === 'dedupe'
      ? [`${n(data.file1_rows)} records`]
      : [`File 1 · ${n(data.file1_rows)} records`, `File 2 · ${n(data.file2_rows)} records`]),
    `${n(data.blocking_pairs)} pairs compared`,
  ];
  resultsWarnings = (data.warnings || []).map(warning => `Warning: ${warning}`);
  setResultsCounts(data);
  renderRows();
}

function setResultsCounts(data) {
  resultsCounts = {
    Match: Number(data.predicted_matches || 0),
    Uncertain: Number(data.predicted_uncertain || 0),
    'Non-Match': Number(data.predicted_non_matches || 0),
  };
  showResultsStatus();
}

function showResultsStatus() {
  const n = value => Number(value || 0).toLocaleString();
  lastResultsStatusParts = [
    ...resultsSummaryParts,
    `${n(resultsCounts.Match)} matches`,
    `${n(resultsCounts.Uncertain)} uncertain`,
    `${n(resultsCounts['Non-Match'])} non-matches`,
    ...resultsWarnings,
  ];
  setStatus(lastResultsStatusParts);
}

async function saveCurrentPageLabels() {
  if (!currentJob || !results.length) return null;
  const labels = Object.fromEntries(results.map(row => [row.pair_id, row.predicted_class]));
  const response = await fetch('/api/labels', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ job_id: currentJob, labels })
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Could not save labels');
  return data;
}

function selectedExportClasses() {
  return Array.from(document.querySelectorAll('input[name="exportClass"]:checked')).map(input => input.value);
}

function selectedExportType() {
  return document.querySelector('input[name="exportType"]:checked').value;
}

function exportLabel(type) {
  return {
    file1: 'file1_results',
    file2: 'file2_results',
    linked: 'linked_results'
  }[type] || 'linkage_results';
}

async function saveBlobWithPrompt(blob, suggestedName) {
  if (window.showSaveFilePicker) {
    const handle = await window.showSaveFilePicker({
      suggestedName,
      types: [{ description: 'CSV file', accept: { 'text/csv': ['.csv'] } }]
    });
    const writable = await handle.createWritable();
    await writable.write(blob);
    await writable.close();
    return;
  }
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = suggestedName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

async function runExport() {
  if (!currentJob) return;
  const classes = selectedExportClasses();
  if (!classes.length) {
    setStatus(['Export needs at least one class']);
    return;
  }
  await saveCurrentPageLabels();
  const type = selectedExportType();
  const params = new URLSearchParams({
    job_id: currentJob,
    type,
    classes: classes.join(','),
    include_linkage_details: '1',
  });
  const response = await fetch(`/api/export?${params.toString()}`, { cache: 'no-store' });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ error: 'Export failed' }));
    throw new Error(error.error || 'Export failed');
  }
  const blob = await response.blob();
  const classPart = classes.map(value => value.toLowerCase().replace('-', '_')).join('_');
  await saveBlobWithPrompt(blob, `${exportLabel(type)}_${classPart}.csv`);
  exportModal.classList.remove('open');
  flashStatus(['Export complete', `${classes.join(', ')}`]);
}

async function reclassifyCurrentJobWithSettings() {
  if (!currentJob) return null;
  const response = await fetch('/api/reclassify', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      job_id: currentJob,
      match_threshold: matchThresholdInput.value.trim() || '0.95',
      non_match_threshold: nonMatchThresholdInput.value.trim() || '0.65',
    }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Could not reclassify results');
  return data;
}

function columnsSelectOptions(sideKey, selectedValue = '') {
  const fields = Array.isArray(availableResultFields?.[sideKey]) ? availableResultFields[sideKey] : [];
  const placeholder = sideKey === 'file1' ? 'Choose a File 1 field' : 'Choose a File 2 field';
  return [
    `<option value="">${escapeHtml(placeholder)}</option>`,
    ...fields.map(field => `<option value="${escapeHtml(field)}" ${field === selectedValue ? 'selected' : ''}>${escapeHtml(fieldLabel(field))}</option>`)
  ].join('');
}

function columnPairRowMarkup(column = null) {
  const file1Field = column?.file1Field || '';
  const file2Field = column?.file2Field || '';
  return `
    <div class="columns-row">
      <label>
        File 1
        <select class="columns-file1-select">${columnsSelectOptions('file1', file1Field)}</select>
      </label>
      <label>
        File 2
        <select class="columns-file2-select">${columnsSelectOptions('file2', file2Field)}</select>
      </label>
      <button class="secondary remove-columns-pair-button" type="button" aria-label="Remove column pair" title="Remove column pair">Remove</button>
    </div>
  `;
}

function refreshColumnsHint() {
  columnsHint.textContent = columnsOptions.querySelector('.columns-row')
    ? ''
    : 'No extra columns yet.';
}

function renderColumnOptions() {
  columnsOptions.innerHTML = (extraVisibleResultColumns || []).map(column => columnPairRowMarkup(column)).join('');
  refreshColumnsHint();
}

function selectedVisibleColumnsFromEditor() {
  const columns = [];
  columnsOptions.querySelectorAll('.columns-row').forEach(row => {
    const file1Field = row.querySelector('.columns-file1-select')?.value || '';
    const file2Field = row.querySelector('.columns-file2-select')?.value || '';
    if (!file1Field || !file2Field) return;
    columns.push(buildVisibleColumn(file1Field, file2Field));
  });
  return columns;
}

function summarizeBlockingSql(sql) {
  const text = String(sql || '').trim();
  return text || 'No SQL condition entered';
}

function blockingSqlRows(sql) {
  return Math.min(4, Math.max(1, Math.ceil(String(sql || '').length / 90)));
}

function blockingRuleCardMarkup(rule, index, checked = true) {
  const sql = rule.sql || '';
  const pairCount = Number(rule.estimated_pairs || 0);
  const title = rule.label || `Rule ${String(index).replace('manual_', '')}`;
  return `
    <div class="blocking-rule-card" data-rule-card="${index}" data-label="${escapeHtml(rule.label || '')}" data-original-sql="${escapeHtml(sql)}">
      <div class="blocking-rule-header">
        <label class="rule-checkbox">
          <input type="checkbox" name="blockingRule" value="${index}" ${checked ? 'checked' : ''}>
          <span class="rule-title">${escapeHtml(title)}</span>
        </label>
        <span class="rule-metric">${pairCount ? `about ${pairCount.toLocaleString()} pairs` : ''}</span>
        <div class="blocking-rule-actions">
          <button class="secondary icon-action remove-action remove-rule-button" type="button" title="Remove rule" aria-label="Remove rule: ${escapeHtml(title)}">×</button>
        </div>
      </div>
      <div class="blocking-conditions">
        <label>SQL condition
          <textarea class="blocking-sql-input" rows="${blockingSqlRows(sql)}" spellcheck="false" placeholder='l."Column" = r."Column"'>${escapeHtml(sql)}</textarea>
        </label>
        ${rule.reason ? `<small class="modal-help">${escapeHtml(rule.reason)}</small>` : ''}
      </div>
    </div>
  `;
}

function refreshBlockingRuleCard(card) {
  // An edited rule's pair estimate no longer applies; say so rather than show a stale number.
  const input = card.querySelector('.blocking-sql-input');
  const metric = card.querySelector('.rule-metric');
  if (input) input.rows = blockingSqlRows(input.value);
  if (metric && input && input.value.trim() !== (card.dataset.originalSql || '').trim()) {
    metric.textContent = 'edited';
  }
}

function selectedBlockingRulesFromEditor() {
  return Array.from(blockingOptions.querySelectorAll('.blocking-rule-card')).map(card => {
    const checkbox = card.querySelector('input[name="blockingRule"]');
    const sql = card.querySelector('.blocking-sql-input')?.value.trim() || '';
    const unchanged = sql === (card.dataset.originalSql || '').trim();
    return {
      enabled: Boolean(checkbox && checkbox.checked),
      sql,
      label: unchanged && card.dataset.label ? card.dataset.label : summarizeBlockingSql(sql),
    };
  }).filter(rule => rule.enabled && rule.sql).map(rule => ({
    label: rule.label,
    sql: rule.sql,
    reason: '',
  }));
}

function hasIncompleteSelectedBlockingRule() {
  return Array.from(blockingOptions.querySelectorAll('.blocking-rule-card')).some(card => {
    const checkbox = card.querySelector('input[name="blockingRule"]');
    if (!(checkbox && checkbox.checked)) return false;
    return !(card.querySelector('.blocking-sql-input')?.value || '').trim();
  });
}

function appendBlockingRuleRow(rule = { sql: '' }, checked = false) {
  blockingRuleRowCounter += 1;
  const addOr = blockingOptions.children.length > 0;
  const html = `${addOr ? '<div class="blocking-or">OR</div>' : ''}${blockingRuleCardMarkup(rule, `manual_${blockingRuleRowCounter}`, checked)}`;
  blockingOptions.insertAdjacentHTML('beforeend', html);
}

function renderBlockingOptions(plan) {
  const rules = plan.blocking_rules || [];
  blockingRuleRowCounter = 0;
  const parts = [];
  rules.forEach((rule, index) => {
    if (index > 0) parts.push('<div class="blocking-or">OR</div>');
    parts.push(blockingRuleCardMarkup(rule, index + 1, true));
  });
  blockingOptions.innerHTML = parts.join('');
}

async function cancelableFetch(url, options = {}) {
  if (workflowStopRequested) throw new Error('Linkage canceled');
  const controller = new AbortController();
  activeAbortController = controller;
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } catch (error) {
    if (error && error.name === 'AbortError') {
      throw new Error('Linkage canceled');
    }
    throw error;
  } finally {
    if (activeAbortController === controller) activeAbortController = null;
  }
}

async function runSchemaMapping(formData) {
  updateProgress(5, 'Mapping and standardizing your files...');
  const { response, data } = await postAgentJob('/api/schema_mapping', formData);
  if (!response.ok) throw new Error(data.error || 'Schema mapping failed');
  const schemaMappingJobId = data.job_id;
  if (!schemaMappingJobId) throw new Error('Schema mapping did not start');
  currentSchemaMappingJob = schemaMappingJobId;
  lastSchemaMappingJob = schemaMappingJobId;
  try {
    while (true) {
      if (workflowStopRequested) throw new Error('Linkage canceled');
      const progressResponse = await cancelableFetch(`/api/progress?job_id=${encodeURIComponent(schemaMappingJobId)}`, { cache: 'no-store' });
      const progress = await progressResponse.json();
      if (!progressResponse.ok) throw new Error(progress.error || 'Could not read schema mapping progress');
      updateProgress(progress.progress, progress.message);
      if (progress.status === 'canceled') throw new Error('Linkage canceled');
      if (progress.status === 'error') throw new Error(progress.error || 'Schema mapping failed');
      if (progress.status === 'done') break;
      await sleep(700);
    }
    const resultResponse = await cancelableFetch(`/api/schema_mapping_result?job_id=${encodeURIComponent(schemaMappingJobId)}`, { cache: 'no-store' });
    const result = await resultResponse.json();
    if (!resultResponse.ok) throw new Error(result.error || 'Could not load schema mapping results');
    return result;
  } finally {
    currentSchemaMappingJob = null;
  }
}

async function analyzeBlockingRules() {
  updateProgress(8, 'Generating blocking rules...');
  const formData = new FormData();
  formData.set('mode', modeSelect.value);
  formData.set('schema_mapping_job_id', lastSchemaMappingJob || currentSchemaMappingJob || '');
  const _blockingMode = modeSelect.value;
  const _blockingSelected = _blockingMode === 'dedupe'
    ? (pendingSchemaMapping?.suggested_fields || [])
    : ((pendingSchemaMapping?.available_fields) || []).map(opt => opt.field);
  formData.set('selected_fields', JSON.stringify(_blockingSelected));
  formData.set('available_fields', JSON.stringify((pendingSchemaMapping && pendingSchemaMapping.available_fields) || []));
  formData.set('api_url', normalizeApiBaseUrl(apiUrlInput.value.trim()));
  formData.set('api_model', apiModelInput.value.trim());
  const { response: startResponse, data: startData } = await postAgentJob('/api/blocking_rules', formData);
  if (!startResponse.ok) throw new Error(startData.error || 'AI blocking-rule generation failed');
  const blockingJobId = startData.job_id;
  if (!blockingJobId) throw new Error('Blocking job did not start');
  currentBlockingJob = blockingJobId;
  try {
    await pollJob(blockingJobId);
    const resultResponse = await cancelableFetch(`/api/blocking_result?job_id=${encodeURIComponent(blockingJobId)}`, { cache: 'no-store' });
    const data = await resultResponse.json();
    if (!resultResponse.ok) throw new Error(data.error || 'Could not load blocking plan');
    return data;
  } finally {
    currentBlockingJob = null;
  }
}

function comparatorLabel(name) {
  return {
    ExactMatch: 'Exact match',
    JaroWinklerAtThresholds: 'Similar text (Jaro-Winkler)',
    JaroAtThresholds: 'Similar text (Jaro)',
    LevenshteinAtThresholds: 'Typos (Levenshtein)',
    DamerauLevenshteinAtThresholds: 'Typos and swapped characters (Damerau-Levenshtein)',
    JaccardAtThresholds: 'Shared words (Jaccard)',
    AbsoluteDateDifferenceAtThresholds: 'Days apart',
    NameComparison: 'Person name',
    DateOfBirthComparison: 'Date of birth',
    EmailComparison: 'Email address',
    PostcodeComparison: 'Postcode / ZIP code'
  }[name] || name;
}

function comparatorSupportsThresholds(name) {
  return ['JaroWinklerAtThresholds', 'JaroAtThresholds', 'LevenshteinAtThresholds', 'DamerauLevenshteinAtThresholds', 'JaccardAtThresholds', 'AbsoluteDateDifferenceAtThresholds'].includes(name);
}

function comparatorThresholdHint(name) {
  if (['LevenshteinAtThresholds', 'DamerauLevenshteinAtThresholds'].includes(name)) return 'Enter integers, e.g. 1, 2';
  if (name === 'AbsoluteDateDifferenceAtThresholds') return 'Enter integers (days), e.g. 7, 30';
  return 'Enter decimals between 0 and 1, e.g. 0.92, 0.82';
}

function comparatorThresholdHelp(name) {
  if (['LevenshteinAtThresholds', 'DamerauLevenshteinAtThresholds'].includes(name)) {
    return 'Most typos allowed at each level, e.g. 1, 2. Smaller is stricter.';
  }
  if (name === 'AbsoluteDateDifferenceAtThresholds') {
    return 'Most days apart at each level, e.g. 7, 30. Smaller is stricter.';
  }
  return 'Lowest similarity (0 to 1) at each level, e.g. 0.92, 0.82. Higher is stricter.';
}

function comparatorThresholdValue(field, comparatorName, selectedThresholds = []) {
  const defaults = (((pendingComparisonPlan || {}).threshold_defaults || {})[comparatorName] || []);
  const source = Array.isArray(selectedThresholds) && selectedThresholds.length ? selectedThresholds : defaults;
  return source.join(', ');
}

async function analyzeComparators(selectedFields) {
  updateProgress(8, 'Selecting comparison methods...');
  const formData = new FormData();
  formData.set('mode', modeSelect.value);
  formData.set('schema_mapping_job_id', lastSchemaMappingJob || currentSchemaMappingJob || '');
  formData.set('selected_fields', JSON.stringify(selectedFields));
  formData.set('available_fields', JSON.stringify((pendingSchemaMapping && pendingSchemaMapping.available_fields) || []));
  formData.set('api_url', normalizeApiBaseUrl(apiUrlInput.value.trim()));
  formData.set('api_model', apiModelInput.value.trim());
  const { response: startResponse, data: startData } = await postAgentJob('/api/comparators', formData);
  if (!startResponse.ok) throw new Error(startData.error || 'AI comparator selection failed');
  const comparatorJobId = startData.job_id;
  if (!comparatorJobId) throw new Error('Comparator job did not start');
  currentComparatorJob = comparatorJobId;
  try {
    await pollJob(comparatorJobId);
    const resultResponse = await cancelableFetch(`/api/comparators_result?job_id=${encodeURIComponent(comparatorJobId)}`, { cache: 'no-store' });
    const data = await resultResponse.json();
    if (!resultResponse.ok) throw new Error(data.error || 'Could not load comparison plan');
    return data;
  } finally {
    currentComparatorJob = null;
  }
}

function comparatorOptionsForField(field, selectedComparator = '') {
  const options = (((pendingComparisonPlan || {}).allowed_comparators || {})[field] || []);
  const chosen = selectedComparator || (options.includes('ExactMatch') ? 'ExactMatch' : (options[0] || ''));
  return options.map(name => `
    <option value="${escapeHtml(name)}" ${name === chosen ? 'selected' : ''}>${escapeHtml(comparatorLabel(name))}</option>
  `).join('');
}

function renderComparatorOptions(plan) {
  const comparisons = plan.comparisons || {};
  const selectedSet = new Set(pendingSelectedFields || []);
  const allFields = sortByDisplayOrder(((pendingSchemaMapping && pendingSchemaMapping.available_fields) || []).map(option => option.field));
  comparatorOptions.innerHTML = allFields.map(field => {
    const selectedComparator = (comparisons[field] || {}).comparator || '';
    const selectedThresholds = (comparisons[field] || {}).thresholds || [];
    const reason = (comparisons[field] || {}).reason || '';
    return `
      <label class="choice-row comparator-row">
        <input type="checkbox" class="comparator-include" data-field="${escapeHtml(field)}" ${selectedSet.has(field) ? 'checked' : ''}>
        <span class="comparator-details">
          <strong>${escapeHtml(fieldLabel(field))}</strong>
          <span class="comparator-meta">${escapeHtml(fieldMetaSummary(field))}</span>
          ${reason ? `<span class="comparator-reason">${escapeHtml(reason)}</span>` : ''}
        </span>
        <div class="comparator-controls">
          <select class="comparator-select" data-field="${escapeHtml(field)}">
            ${comparatorOptionsForField(field, selectedComparator)}
          </select>
          <label class="comparator-threshold-wrap" ${comparatorSupportsThresholds(selectedComparator) ? '' : 'style="display:none;"'}>
            <span class="comparator-threshold-label">Thresholds</span>
            <input class="comparator-thresholds" data-field="${escapeHtml(field)}" type="text" value="${escapeHtml(comparatorThresholdValue(field, selectedComparator, selectedThresholds))}" placeholder="${escapeHtml(comparatorThresholdHint(selectedComparator || 'JaroWinklerAtThresholds'))}">
            <small class="comparator-threshold-help">${escapeHtml(comparatorThresholdHelp(selectedComparator || 'JaroWinklerAtThresholds'))}</small>
          </label>
        </div>
      </label>
    `;
  }).join('');
}

function selectedComparisonPlanFromEditor() {
  const comparisons = {};
  const selectedFields = [];
  comparatorOptions.querySelectorAll('.choice-row').forEach(row => {
    const checkbox = row.querySelector('.comparator-include');
    const select = row.querySelector('.comparator-select');
    const thresholdsInput = row.querySelector('.comparator-thresholds');
    if (!(checkbox && checkbox.checked && select && select.value)) return;
    selectedFields.push(select.dataset.field);
    comparisons[select.dataset.field] = {
      comparator: select.value,
      thresholds: comparatorSupportsThresholds(select.value)
        ? String(thresholdsInput?.value || '').split(',').map(item => item.trim()).filter(Boolean)
        : [],
      reason: ''
    };
  });
  return { selected_fields: selectedFields, comparisons };
}

function sortByDisplayOrder(fields) {
  const order = Array.isArray(pendingSchemaMapping?.display_order) ? pendingSchemaMapping.display_order : [];
  if (!order.length) return [...fields];
  return [...fields].sort((a, b) => {
    const ai = order.indexOf(a), bi = order.indexOf(b);
    return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi);
  });
}

async function reviewBlockingRules() {
  if (workflowStopRequested) throw new Error('Linkage canceled');
  setWizardStep('blocking');
  pendingSelectedFields = Array.isArray(pendingSchemaMapping?.suggested_fields) ? [...pendingSchemaMapping.suggested_fields] : [];
  linkageFields = sortByDisplayOrder(pendingSelectedFields);
  availableResultFields = {
    file1: Array.isArray(pendingSchemaMapping.standardized_fields1) ? pendingSchemaMapping.standardized_fields1 : [],
    file2: Array.isArray(pendingSchemaMapping.standardized_fields2) ? pendingSchemaMapping.standardized_fields2 : [],
  };
  setStatus(['Generating blocking rules...']);
  pendingBlockingPlan = await analyzeBlockingRules();
  if (workflowStopRequested) throw new Error('Linkage canceled');
  setStatus(blockingStatusParts(pendingBlockingPlan));
  finishProgress('Blocking rules ready.');
  renderBlockingOptions(pendingBlockingPlan);
  comparatorNeedsRefresh = true;
  setWorkflowBusy(false);
  showWorkflowPanel(blockingModal);
}

async function reviewComparatorsForSelectedVariables() {
  if (workflowStopRequested) throw new Error('Linkage canceled');
  setWizardStep('comparator');
  setStatus(['Choosing matching comparators...']);
  pendingComparisonPlan = await analyzeComparators(pendingSelectedFields);
  if (Array.isArray(pendingComparisonPlan.selected_fields)) {
    pendingSelectedFields = sortByDisplayOrder(pendingComparisonPlan.selected_fields);
    linkageFields = [...pendingSelectedFields];
  }
  if (workflowStopRequested) throw new Error('Linkage canceled');
  setStatus(['Review the matching setup']);
  finishProgress('Matching setup ready.');
  renderComparatorOptions(pendingComparisonPlan);
  comparatorNeedsRefresh = false;
  setWorkflowBusy(false);
  showWorkflowPanel(comparatorModal);
}

async function moveFromBlockingToComparators() {
  if (hasIncompleteSelectedBlockingRule()) {
    setStatus(['Each selected blocking rule needs at least one field']);
    return;
  }
  const approvedRules = selectedBlockingRulesFromEditor();
  if (!approvedRules.length) {
    setStatus(['Select at least one blocking rule']);
    return;
  }
  const approvedVariables = Array.from(new Set(Array.from(blockingOptions.querySelectorAll('.blocking-rule-card')).flatMap(card => {
    const checkbox = card.querySelector('input[name="blockingRule"]');
    if (!(checkbox && checkbox.checked)) return [];
    return Array.from(card.querySelectorAll('.blocking-variable-select'))
      .map(select => select.value)
      .filter(Boolean);
  })));
  pendingApprovedBlockingRules = approvedRules;
  pendingSelectedFields = Array.from(new Set([...(pendingSchemaMapping?.suggested_fields || []), ...approvedVariables]));
  hideWorkflowPanels();
  setWorkflowBusy(true);
  startProgress();
  await reviewComparatorsForSelectedVariables();
}

async function startLinkageWithApprovedBlockingRules(autoRun = false) {
  const selectedComparatorState = autoRun
    ? {
        selected_fields: [...pendingSelectedFields],
        excluded_fields: [...((pendingComparisonPlan || {}).excluded_fields || [])],
        comparisons: Object.fromEntries(
          Object.entries((pendingComparisonPlan || {}).comparisons || {})
            .filter(([field]) => pendingSelectedFields.includes(field))
        ),
      }
    : selectedComparisonPlanFromEditor();
  if (!selectedComparatorState.selected_fields.length) {
    setStatus(['Select at least one matching variable']);
    return;
  }
  pendingSelectedFields = sortByDisplayOrder(selectedComparatorState.selected_fields);
  linkageFields = [...pendingSelectedFields];
  pendingComparisonPlan = {
    ...(pendingComparisonPlan || {}),
    excluded_fields: selectedComparatorState.excluded_fields || [],
    selected_fields: selectedComparatorState.selected_fields,
    comparisons: selectedComparatorState.comparisons,
  };
  setWizardStep('results');
  hideWorkflowPanels();
  results = [];
  totalFilteredResults = 0;
  totalPages = 1;
  currentPage = 1;
  lastResultsStatusParts = [];
  currentJob = null;
  const formData = new FormData();
  for (const [key, value] of pendingFormData.entries()) formData.append(key, value);
  formData.set('selected_fields', JSON.stringify(pendingSelectedFields));
  formData.set('available_fields', JSON.stringify((pendingSchemaMapping && pendingSchemaMapping.available_fields) || []));
  formData.set('comparison_plan', JSON.stringify(pendingComparisonPlan || {}));
  formData.set('comparison_ai_log_path', (pendingComparisonPlan && pendingComparisonPlan.ai_log_path) || '');
  formData.set('blocking_rules', JSON.stringify(pendingApprovedBlockingRules || []));
  formData.set('blocking_ai_log_path', pendingBlockingPlan.ai_log_path || '');
  formData.set('shared_field_plan', JSON.stringify(pendingSchemaMapping.shared_field_plan || {}));
  formData.set('schema_mapping_job_id', lastSchemaMappingJob || '');
  await submitLinkageJob(formData);
}

async function runSuperEzWorkflow() {
  applyManualMappingsToSchemaMapping();
  hideWorkflowPanels();
  setWorkflowBusy(true);
  startProgress();
  setWizardStep('preview');
  updateProgress(36, 'Accepting standardized fields...');
  pendingSelectedFields = sortByDisplayOrder(Array.isArray(pendingSchemaMapping?.suggested_fields) ? pendingSchemaMapping.suggested_fields : []);
  linkageFields = [...pendingSelectedFields];
  availableResultFields = {
    file1: Array.isArray(pendingSchemaMapping.standardized_fields1) ? pendingSchemaMapping.standardized_fields1 : [],
    file2: Array.isArray(pendingSchemaMapping.standardized_fields2) ? pendingSchemaMapping.standardized_fields2 : [],
  };
  setWizardStep('blocking');
  updateProgress(42, 'Generating blocking rules...');
  setStatus(['Super EZ Mode', 'Blocking in progress']);
  pendingBlockingPlan = await analyzeBlockingRules();
  resetProgress();
  pendingApprovedBlockingRules = [...(pendingBlockingPlan.blocking_rules || [])];
  if (!pendingApprovedBlockingRules.length) throw new Error('AI blocking-rule generation returned no rules.');
  renderBlockingOptions(pendingBlockingPlan);
  setWizardStep('comparator');
  updateProgress(64, 'Selecting comparison methods...');
  setStatus(['Super EZ Mode', 'Matching in progress']);
  pendingComparisonPlan = await analyzeComparators(pendingSelectedFields);
  resetProgress();
  renderComparatorOptions(pendingComparisonPlan);
  setWizardStep('results');
  updateProgress(82, 'Running record linkage...');
  await startLinkageWithApprovedBlockingRules(true);
}

async function submitLinkageJob(formData) {
  setWorkflowBusy(true);
  startProgress();
  const response = await cancelableFetch('/api/link', { method: 'POST', body: formData });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Linkage failed');
  currentJob = data.job_id;
  stopJobButton.disabled = false;
  setStatus(['Running linkage...']);
  updateProgress(12, 'Starting linkage...');
  await pollJob(currentJob);
  updateProgress(99, 'Loading results...');
  currentPage = 1;
  searchBox.disabled = false;
  classFilter.disabled = false;
  highlightDiffToggle.disabled = false;
  saveButton.disabled = false;
  columnsButton.disabled = false;
  exportMenuButton.disabled = false;
  await fetchResultsPage(1);
  setWorkflowBusy(false);
  workflowStopRequested = false;
  finishProgress('Linkage complete.');
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, m => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;'
  }[m]));
}

function blockingStatusParts(plan) {
  if (!plan) return ['Review the blocking rules'];
  const rules = plan.blocking_rules || [];
  const metrics = plan.blocking_metrics || {};
  const proj = metrics.projected_union_pairs;
  const samp = metrics.union_pairs;
  const pairs = proj != null ? proj : samp;
  const parts = [`${rules.length} rule${rules.length !== 1 ? 's' : ''}`];
  if (pairs != null) parts.push(`about ${Number(pairs).toLocaleString()} pairs to compare`);
  return parts;
}

// Colour carries meaning only for match status and problems; everything else is a neutral pill.
function statusPillClass(part) {
  if (/^error\b/i.test(part)) return ' pill-danger';
  if (/^[\d,]+ non-matches?$/i.test(part)) return ' pill-danger';
  if (/^[\d,]+ uncertain$/i.test(part)) return ' pill-warning';
  if (/^[\d,]+ matches?$/i.test(part)) return ' pill-success';
  return '';
}

// Say what the running step is doing, rather than a generic "working" line.
const BUSY_CAPTIONS = {
  preview: ['Lining up the fields', 'Reading column names and sample values, and checking formats such as dates.'],
  blocking: ['Choosing which pairs to compare', 'Testing candidate rules on a sample to see how many pairs each one keeps.'],
  comparator: ['Choosing how to compare each field', 'Picking a comparison for each field based on what its values look like.'],
  results: ['Scoring candidate pairs', 'Splink is estimating field weights and scoring every pair the blocking rules kept.'],
};

function setBusyCaption(step) {
  const [title, sub] = BUSY_CAPTIONS[step] || ['Working…', ''];
  const titleEl = document.getElementById('processingCaptionTitle');
  const subEl = document.getElementById('processingCaptionSub');
  if (titleEl) titleEl.textContent = title;
  if (subEl) subEl.textContent = sub;
}

function setStatus(parts) {
  if (statusResetTimer) {
    clearTimeout(statusResetTimer);
    statusResetTimer = null;
  }
  statusBox.innerHTML = parts.map(part => {
    if (/^warning:/i.test(part)) {
      return `<p class="status-note">${escapeHtml(part)}</p>`;
    }
    return `<span class="pill${statusPillClass(part)}">${escapeHtml(part)}</span>`;
  }).join('');
}

function defaultStatusParts() {
  if (document.body.classList.contains('busy')) {
    return null;
  }
  if (currentWizardStep === 'upload') return ['Ready for input files.'];
  if (currentWizardStep === 'preview') return ['Review the schema mapping'];
  if (currentWizardStep === 'blocking') return blockingStatusParts(pendingBlockingPlan);
  if (currentWizardStep === 'comparator') return ['Review the matching setup'];
  if (currentWizardStep === 'results' && currentJob && !results.length) return ['Running linkage...'];
  if (currentWizardStep === 'results' && !results.length) return ['Review linkage results'];
  if (currentWizardStep === 'results' && lastResultsStatusParts.length) return lastResultsStatusParts;
  if (currentWizardStep === 'results' && results.length) return [`${totalFilteredResults || results.length} pairs compared`];
  return ['Ready'];
}

function flashStatus(parts, duration = 2200) {
  setStatus(parts);
  statusResetTimer = setTimeout(() => {
    statusResetTimer = null;
    const fallback = defaultStatusParts();
    if (fallback) setStatus(fallback);
  }, duration);
}

function markBlockingDirty() {
  comparatorNeedsRefresh = true;
}

function canNavigateToStep(step) {
  if (document.body.classList.contains('busy')) {
    return false;
  }
  if (step === 'upload') return true;
  if (step === 'preview') return Boolean(pendingSchemaMapping);
  if (step === 'blocking') return Boolean(pendingBlockingPlan);
  if (step === 'comparator') return Boolean(pendingBlockingPlan || pendingComparisonPlan);
  if (step === 'results') return Boolean(currentJob || results.length || totalFilteredResults);
  return false;
}

async function navigateToStep(step) {
  if (!canNavigateToStep(step)) return;
  if (step === 'upload') {
    hideWorkflowPanels();
    setResultsVisible(false);
    setWizardStep('upload');
    return;
  }
  if (step === 'preview') {
    if (!pendingSchemaMapping) return;
    renderPreviewContent(pendingSchemaMapping);
    showWorkflowPanel(previewModal);
    return;
  }
  if (step === 'blocking') {
    if (!pendingBlockingPlan) return;
    renderBlockingOptions(pendingBlockingPlan);
    showWorkflowPanel(blockingModal);
    return;
  }
  if (step === 'comparator') {
    if (comparatorNeedsRefresh || !pendingComparisonPlan) {
      try {
        await moveFromBlockingToComparators();
      } catch (error) {
          failProgress(error.message);
        setWorkflowBusy(false);
      }
      return;
    }
    renderComparatorOptions(pendingComparisonPlan);
    showWorkflowPanel(comparatorModal);
    return;
  }
  if (step === 'results') {
    if (currentJob || results.length || totalFilteredResults) {
      hideWorkflowPanels();
      setResultsVisible(true);
    }
  }
}

// One name per step, used by the sidebar, panel titles and status labels alike.
const STEP_NAMES = {
  upload: 'Upload',
  preview: 'Schema Mapping',
  blocking: 'Blocking',
  comparator: 'Matching',
  results: 'Results',
};

function setWizardStep(step) {
  const order = ['upload', 'preview', 'blocking', 'comparator', 'results'];
  const labels = {
    upload: 'Step 1 of 5: Upload.',
    preview: 'Step 2 of 5: Schema Mapping.',
    blocking: 'Step 3 of 5: Blocking.',
    comparator: 'Step 4 of 5: Matching.',
    results: 'Step 5 of 5: Results.',
  };
  const currentIndex = order.indexOf(step);
  stepper.querySelectorAll('.step-card').forEach((card, index) => {
    card.classList.toggle('active', index === currentIndex);
    card.classList.toggle('done', currentIndex > index);
    card.classList.toggle('available', canNavigateToStep(card.dataset.step));
    card.setAttribute('aria-current', index === currentIndex ? 'step' : 'false');
  });
  stepStatus.textContent = labels[step] || '';
  const pageTitle = document.getElementById('pageTitle');
  const pageStep = document.getElementById('pageStep');
  if (pageTitle) pageTitle.textContent = STEP_NAMES[step] || '';
  if (pageStep) pageStep.textContent = currentIndex >= 0 ? `Step ${currentIndex + 1} of 5` : '';
  currentWizardStep = step;
  document.body.classList.toggle('step-results', step === 'results');
  setBusyCaption(step);
  uploadPanel.classList.toggle('hidden', step !== 'upload');
  workflowShell.classList.toggle('hidden', !['preview', 'blocking', 'comparator'].includes(step));
  resultsShell.classList.toggle('hidden', step !== 'results');
  if (progressShell.parentElement !== liveProgressHost) {
    liveProgressHost.appendChild(progressShell);
  }
  if (document.body.classList.contains('busy')) {
    // While a step runs, say which one; the progress line underneath gives the detail.
    setStatus([step === 'results' ? 'Running linkage' : `${STEP_NAMES[step]} in progress`]);
  } else {
    const fallback = defaultStatusParts();
    if (fallback) setStatus(fallback);
  }
}

function showWorkflowPanel(panel) {
  const stepMap = new Map([
    [previewModal, 'preview'],
    [blockingModal, 'blocking'],
    [comparatorModal, 'comparator'],
  ]);
  workflowShell.classList.add('active');
  workflowShell.hidden = false;
  [previewModal, blockingModal, comparatorModal].forEach(node => node.classList.toggle('open', node === panel));
  setWizardStep(stepMap.get(panel) || 'preview');
}

function hideWorkflowPanels() {
  [previewModal, blockingModal, comparatorModal].forEach(node => node.classList.remove('open'));
  workflowShell.classList.remove('active');
  workflowShell.hidden = true;
}

function setResultsVisible(visible) {
  resultsShell.classList.toggle('hidden', !visible);
  resultsShell.classList.toggle('has-results', visible && Boolean(totalCandidatePairs));
  if (visible) setWizardStep('results');
}

stepper.querySelectorAll('.step-card').forEach(card => {
  card.addEventListener('click', () => { void navigateToStep(card.dataset.step); });
  card.addEventListener('keydown', event => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      void navigateToStep(card.dataset.step);
    }
  });
});

const CELL_MAX_CHARS = 160;
function truncateCell(value) {
  const s = value == null ? '' : String(value);
  return s.length > CELL_MAX_CHARS ? s.slice(0, CELL_MAX_CHARS) + '…' : s;
}

function isBlankValue(value) {
  return value === null || value === undefined || String(value).trim() === '';
}

function pairCell(left, right, leftClass = 'source-a', rightClass = 'source-b') {
  return `<div class="pair-values"><span class="${leftClass}">${escapeHtml(truncateCell(left))}</span><span class="${rightClass}">${escapeHtml(truncateCell(right))}</span></div>`;
}

// How a compared field agrees, from Splink's comparison level for the pair.
// A blank on either side is missing information, not a disagreement.
const AGREEMENT = {
  agree: { symbol: '✓', label: 'Agrees' },
  partial: { symbol: '≈', label: 'Partly agrees' },
  differ: { symbol: '✗', label: 'Disagrees' },
  missing: { symbol: '–', label: 'Missing in one file' },
};

function agreementLevel(left, right, score) {
  if (score === null || score === undefined || Number.isNaN(Number(score))) return null;
  if (isBlankValue(left) || isBlankValue(right)) return 'missing';
  const numeric = Number(score);
  if (numeric >= 0.999) return 'agree';
  if (numeric <= 0.001) return 'differ';
  return 'partial';
}

const DIFF_MAX_CELLS = 40000;

// Marks the characters that differ between the two values (case is ignored). The values
// are aligned with the fewest edits, counting a substitution, insertion, deletion or swap
// of two neighbouring characters as one edit (optimal string alignment, the restricted
// Damerau-Levenshtein distance), so a mistyped character is marked where it is.
function differenceMarkup(left, right) {
  const a = Array.from(left), b = Array.from(right);
  const same = (x, y) => x.toLowerCase() === y.toLowerCase();
  if (!a.length || !b.length || a.length * b.length > DIFF_MAX_CELLS) return [escapeHtml(left), escapeHtml(right)];
  const d = Array.from({ length: a.length + 1 }, () => new Uint16Array(b.length + 1));
  for (let i = 0; i <= a.length; i++) d[i][0] = i;
  for (let j = 0; j <= b.length; j++) d[0][j] = j;
  const swapped = (i, j) => i > 1 && j > 1 && same(a[i - 1], b[j - 2]) && same(a[i - 2], b[j - 1]);
  for (let i = 1; i <= a.length; i++) {
    for (let j = 1; j <= b.length; j++) {
      d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (same(a[i - 1], b[j - 1]) ? 0 : 1));
      if (swapped(i, j)) d[i][j] = Math.min(d[i][j], d[i - 2][j - 2] + 1);
    }
  }
  const markA = new Array(a.length).fill(false), markB = new Array(b.length).fill(false);
  for (let i = a.length, j = b.length; i > 0 || j > 0;) {
    if (i > 0 && j > 0 && same(a[i - 1], b[j - 1]) && d[i][j] === d[i - 1][j - 1]) { i--; j--; }
    else if (i > 0 && j > 0 && d[i][j] === d[i - 1][j - 1] + 1) { markA[i - 1] = markB[j - 1] = true; i--; j--; }
    else if (swapped(i, j) && d[i][j] === d[i - 2][j - 2] + 1) { markA[i - 1] = markA[i - 2] = markB[j - 1] = markB[j - 2] = true; i -= 2; j -= 2; }
    else if (i > 0 && d[i][j] === d[i - 1][j] + 1) { markA[i - 1] = true; i--; }
    else { markB[j - 1] = true; j--; }
  }
  const paint = (chars, marks) => {
    let out = '', open = false;
    chars.forEach((ch, index) => {
      if (marks[index] && !open) { out += '<mark>'; open = true; }
      if (!marks[index] && open) { out += '</mark>'; open = false; }
      out += escapeHtml(ch);
    });
    return open ? `${out}</mark>` : out;
  };
  return [paint(a, markA), paint(b, markB)];
}

function fieldValuesMarkup(left, right, level) {
  const blank = '<span class="blank">blank</span>';
  const a = truncateCell(left), b = truncateCell(right);
  let [htmlA, htmlB] = [isBlankValue(a) ? blank : escapeHtml(a), isBlankValue(b) ? blank : escapeHtml(b)];
  if ((level === 'agree' || level === 'partial') && a.toLowerCase() !== b.toLowerCase()) [htmlA, htmlB] = differenceMarkup(a, b);
  return `<div class="pair-values"><span class="source-a">${htmlA}</span><span class="source-b">${htmlB}</span></div>`;
}

function sourceReference(row, side) {
  const rowIdx = row[`${side}_row_index`] || '';
  return `Record ${rowIdx}`;
}

function sharedFieldOptions() {
  return Array.isArray((pendingSchemaMapping || {}).available_fields) ? pendingSchemaMapping.available_fields : [];
}

function sharedFieldByName(field) {
  return sharedFieldOptions().find(option => option.field === field) || null;
}

function sharedFieldBySide(sideKey, fieldName) {
  const lookupKey = sideKey === 'file1' ? 'file1_field' : 'file2_field';
  return sharedFieldOptions().find(option => option[lookupKey] === fieldName) || null;
}

function buildVisibleColumn(file1Field, file2Field) {
  const shared = sharedFieldOptions().find(option => option.file1_field === file1Field && option.file2_field === file2Field);
  const label = shared
    ? fieldLabel(shared.field)
    : `${fieldLabel(file1Field)} / ${fieldLabel(file2Field)}`;
  const inferredScoreField = shared ? shared.field : (file1Field === file2Field && linkageFields.includes(file1Field) ? file1Field : null);
  return {
    key: shared ? `shared:${shared.field}` : `pair:${file1Field}::${file2Field}`,
    label,
    file1Field,
    file2Field,
    scoreField: inferredScoreField,
  };
}

function defaultVisibleColumnsFromFields(fields) {
  return (fields || []).map(field => {
    const shared = sharedFieldByName(field);
    if (shared) {
      return buildVisibleColumn(shared.file1_field || field, shared.file2_field || field);
    }
    return buildVisibleColumn(field, field);
  });
}

function displayedResultColumns() {
  const combined = [...defaultVisibleColumnsFromFields(linkageFields), ...(extraVisibleResultColumns || [])];
  const seen = new Set();
  return combined.filter(column => {
    if (!column || seen.has(column.key)) return false;
    seen.add(column.key);
    return true;
  });
}

function resultColumnCount() {
  return 4 + displayedResultColumns().length;
}

function fieldScore(row, field) {
  if (!field || !linkageFields.includes(field)) return null;
  if (row.field_scores && Object.prototype.hasOwnProperty.call(row.field_scores, field)) {
    return row.field_scores[field];
  }
  return null;
}

function fieldCell(row, column) {
  const left = row.file1[column.file1Field], right = row.file2[column.file2Field];
  const score = fieldScore(row, column.scoreField);
  const level = agreementLevel(left, right, score);
  // Columns added with "Columns" were not compared by the model, so they carry no agreement.
  if (!level) return `<td>${fieldValuesMarkup(left, right, null)}</td>`;
  const { symbol, label } = AGREEMENT[level];
  const note = level === 'partial' ? `<div class="similarity-note">Similarity ${Number(score).toFixed(2)}</div>` : '';
  return `<td class="fv fv-${level}"><span class="agree-mark" title="${label}" aria-label="${label}">${symbol}</span>${fieldValuesMarkup(left, right, level)}${note}</td>`;
}

function renderTableHeader() {
  const staticHeaders = ['#', 'Status', 'Match probability', 'Records'];
  resultsHead.innerHTML = `<tr>${[
    ...staticHeaders,
    ...displayedResultColumns().map(column => column.label)
  ].map(label => `<th>${escapeHtml(label)}</th>`).join('')}</tr>`;
}

// Colour follows the same thresholds that decide Match / Uncertain / Non-Match.
function probabilityClass(value) {
  if (value >= Number(matchThresholdInput.value || 0.95)) return 'high';
  if (value >= Number(nonMatchThresholdInput.value || 0.65)) return 'mid';
  return 'low';
}

// Probabilities are estimates, so never claim certainty: 0.9999 is ">99.9%", not "100.00%".
function formatProbability(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '';
  if (numeric >= 0.9995) return '>99.9%';
  if (numeric <= 0.0005) return '<0.1%';
  return `${(numeric * 100).toFixed(1)}%`;
}

function statusRowClass(predictedClass) {
  return { Match: 'row-match', Uncertain: 'row-uncertain', 'Non-Match': 'row-nonmatch' }[predictedClass] || '';
}

function renderRows() {
  renderTableHeader();
  pageStatus.textContent = totalFilteredResults
    ? `Page ${currentPage} of ${totalPages} · ${totalFilteredResults.toLocaleString()} pairs`
    : 'No pairs';
  prevPageButton.disabled = !currentJob || currentPage <= 1;
  nextPageButton.disabled = !currentJob || currentPage >= totalPages || totalFilteredResults === 0;
  resultsShell.classList.toggle('has-results', Boolean(totalCandidatePairs));

  if (!results.length) {
    const hasActiveSearch = searchBox.value.trim() || classFilter.value;
    body.innerHTML = `<tr><td colspan="${resultColumnCount()}" class="empty">${
      hasActiveSearch
        ? 'No pairs match the current filter. <a href="#" id="clearSearchLink">Clear filter</a>'
        : 'No results to display.'
    }</td></tr>`;
    const clearLink = body.querySelector('#clearSearchLink');
    if (clearLink) {
      clearLink.addEventListener('click', e => {
        e.preventDefault();
        searchBox.value = '';
        classFilter.value = 'All';
        fetchResultsPage(1).catch(error => failProgress(error.message));
      });
    }
    return;
  }

  body.innerHTML = results.map((row, i) => `
    <tr data-pair-id="${escapeHtml(row.pair_id)}" class="${statusRowClass(row.predicted_class)}">
      <td class="row-seq">${(currentPage - 1) * pageSize + i + 1}</td>
      <td>
        <select data-pair-id="${escapeHtml(row.pair_id)}">
          <option ${row.predicted_class === 'Match' ? 'selected' : ''}>Match</option>
          <option ${row.predicted_class === 'Uncertain' ? 'selected' : ''}>Uncertain</option>
          <option ${row.predicted_class === 'Non-Match' ? 'selected' : ''}>Non-Match</option>
        </select>
      </td>
      <td class="prob ${probabilityClass(row.posterior_probability)}">${formatProbability(row.posterior_probability)}</td>
      <td>${pairCell(sourceReference(row, 'file1'), sourceReference(row, 'file2'))}</td>
      ${displayedResultColumns().map(column => fieldCell(row, column)).join('')}
    </tr>
  `).join('');
}

form.addEventListener('submit', async event => {
  event.preventDefault();
  workflowStopRequested = false;
  pendingSchemaMapping = null;
  pendingPreviewRows1 = [];
  pendingPreviewRows2 = [];
  pendingManualMappings = [];
  pendingComparisonPlan = null;
  pendingBlockingPlan = null;
  pendingApprovedBlockingRules = [];
  hideWorkflowPanels();
  setResultsVisible(false);
  currentJob = null;
  results = [];
  lastResultsStatusParts = [];
  totalFilteredResults = 0;
  totalCandidatePairs = 0;
  totalPages = 1;
  currentPage = 1;
  searchBox.value = '';
  classFilter.value = 'All';
  const formData = new FormData(form);
  formData.set('api_url', normalizeApiBaseUrl(apiUrlInput.value.trim()));
  formData.set('api_model', apiModelInput.value.trim());
  formData.set('match_threshold', matchThresholdInput.value.trim() || '0.95');
  formData.set('non_match_threshold', nonMatchThresholdInput.value.trim() || '0.65');
  pendingFormData = formData;
  currentWizardStep = 'preview';
  setWorkflowBusy(true);
  startProgress();
  searchBox.disabled = true;
  classFilter.disabled = true;
  highlightDiffToggle.disabled = true;
  saveButton.disabled = true;
  columnsButton.disabled = true;
  prevPageButton.disabled = true;
  nextPageButton.disabled = true;
  exportMenuButton.disabled = true;
  linkageFields = [];
  extraVisibleResultColumns = [];
  availableResultFields = { file1: [], file2: [] };
  pageStatus.textContent = '';

  try {
    pendingSchemaMapping = await runSchemaMapping(formData);
    if (workflowStopRequested) throw new Error('Linkage canceled');
    availableFieldLabels = Object.fromEntries((pendingSchemaMapping.available_fields || []).map(option => [option.field, option.label || option.field]));
    availableFieldMeta = Object.fromEntries((pendingSchemaMapping.available_fields || []).map(option => [option.field, {
      role: option.role || '',
      profile1: ((pendingSchemaMapping.field_profiles || {}).file1 || {})[option.field] || {},
      profile2: ((pendingSchemaMapping.field_profiles || {}).file2 || {})[option.field] || {}
    }]));
    pendingPreviewRows1 = Array.isArray(pendingSchemaMapping.preview_rows1) ? pendingSchemaMapping.preview_rows1 : [];
    pendingPreviewRows2 = Array.isArray(pendingSchemaMapping.preview_rows2) ? pendingSchemaMapping.preview_rows2 : [];
    renderPreviewContent(pendingSchemaMapping);
    if (superEzModeInput && superEzModeInput.checked) {
      updateProgress(34, 'Schema mapping complete. Continuing automatically...');
      setStatus(['Super EZ Mode', 'Continuing automatically...']);
      await runSuperEzWorkflow();
    } else {
      finishProgress('Schema mapping complete.');
      setWorkflowBusy(false);
      showWorkflowPanel(previewModal);
    }
  } catch (error) {
    const canceled = error.message === 'Linkage canceled';
    setWorkflowBusy(false);
    setResultsVisible(false);
    if (!pendingSchemaMapping) {
      // Schema mapping never finished, so there is nothing to review: go back to Upload,
      // keeping the chosen files, so the user can fix the problem and press Start again.
      hideWorkflowPanels();
      setWizardStep('upload');
    }
    failProgress(canceled ? 'Linkage canceled.' : error.message);
    setStatus(canceled ? ['Linkage canceled'] : ['Error: schema mapping did not complete']);
  }
});

continuePreviewButton.addEventListener('click', () => {
  applyManualMappingsToSchemaMapping();
  hideWorkflowPanels();
  setWorkflowBusy(true);
  startProgress();
  reviewBlockingRules().catch(error => {
    if (error.message === 'Linkage canceled') {
      setStatus(['Linkage canceled']);
      failProgress('Linkage canceled.');
      setWorkflowBusy(false);
    } else {
      failProgress(error.message);
    }
  });
});

cancelPreviewButton.addEventListener('click', () => {
  hideWorkflowPanels();
  pendingManualMappings = [];
  previewMappingRows.innerHTML = '';
  mappingFile1Select.value = '';
  mappingFile2Select.value = '';
  mappingNameInput.value = '';
  setWorkflowBusy(false);
  setStatus(['Ready']);
  setWizardStep('upload');
});

previewModal.addEventListener('click', event => {
  if (event.target === previewModal) {
    event.stopPropagation();
  }
});

previewContent.addEventListener('click', event => {
  const btn = event.target.closest('.link-badge-remove');
  if (!btn || !pendingSchemaMapping) return;
  const field = btn.dataset.removeField;
  if (!field) return;
  const mode = pendingSchemaMapping.mode || modeSelect.value;
  if (mode === 'dedupe') {
    // Only deselect — keep in available_fields so the user can add it back
    pendingSchemaMapping.suggested_fields = (pendingSchemaMapping.suggested_fields || []).filter(f => f !== field);
  } else {
    pendingManualMappings = pendingManualMappings.filter(m => m.shared_name !== field);
    pendingSchemaMapping.available_fields = (pendingSchemaMapping.available_fields || []).filter(opt => opt.field !== field);
    pendingSchemaMapping.suggested_fields = (pendingSchemaMapping.suggested_fields || []).filter(f => f !== field);
    availableFieldLabels = Object.fromEntries((pendingSchemaMapping.available_fields || []).map(opt => [opt.field, opt.label || opt.field]));
    availableFieldMeta = Object.fromEntries((pendingSchemaMapping.available_fields || []).map(opt => [opt.field, {
      role: opt.role || '',
      profile1: opt.profile1 || (((pendingSchemaMapping.field_profiles || {}).file1 || {})[opt.field] || {}),
      profile2: opt.profile2 || (((pendingSchemaMapping.field_profiles || {}).file2 || {})[opt.field] || {}),
    }]));
  }
  renderPreviewContent(pendingSchemaMapping);
});

mappingFile1Select.addEventListener('change', () => {
  if (!mappingNameInput.value.trim()) {
    mappingNameInput.value = mappingFile1Select.value || '';
  }
});

addPreviewMappingButton.addEventListener('click', () => {
  const f1 = mappingFile1Select.value;
  const f2 = mappingFile2Select.value;
  const name = mappingNameInput.value.trim();
  if (!f1 || !f2 || !name) return;
  const alreadyExists = pendingManualMappings.some(m => m.shared_name === name);
  if (!alreadyExists) {
    pendingManualMappings.push({ file1_field: f1, file2_field: f2, shared_name: name });
  }
  mappingFile1Select.value = '';
  mappingFile2Select.value = '';
  mappingNameInput.value = '';
  if (!pendingSchemaMapping) return;
  applyManualMappingsToSchemaMapping();
  _refreshPreviewTable(pendingSchemaMapping);
});

dedupeAddFieldButton.addEventListener('click', () => {
  const field = dedupeAddFieldSelect.value;
  if (!field || !pendingSchemaMapping) return;
  if (!(pendingSchemaMapping.suggested_fields || []).includes(field)) {
    pendingSchemaMapping.suggested_fields = [...(pendingSchemaMapping.suggested_fields || []), field];
  }
  renderPreviewContent(pendingSchemaMapping);
});

backToBlockingButton.addEventListener('click', () => {
  pendingComparisonPlan = {
    ...(pendingComparisonPlan || {}),
    ...selectedComparisonPlanFromEditor(),
  };
  renderBlockingOptions(pendingBlockingPlan);
  showWorkflowPanel(blockingModal);
});

confirmComparatorButton.addEventListener('click', async () => {
  try {
    confirmComparatorButton.disabled = true;
    startProgress();
    await startLinkageWithApprovedBlockingRules();
  } catch (error) {
    if (error.message === 'Linkage canceled') {
      setStatus(['Linkage canceled']);
      failProgress('Linkage canceled.');
      setWorkflowBusy(false);
    } else {
      failProgress(error.message);
    }
  } finally {
    confirmComparatorButton.disabled = false;
  }
});


comparatorModal.addEventListener('click', event => {
  if (event.target === comparatorModal) {
    event.stopPropagation();
  }
});

columnsButton.addEventListener('click', () => {
  if (!currentJob) return;
  renderColumnOptions();
  columnsModal.classList.add('open');
});

cancelColumnsButton.addEventListener('click', () => {
  columnsModal.classList.remove('open');
});

applyColumnsButton.addEventListener('click', () => {
  extraVisibleResultColumns = selectedVisibleColumnsFromEditor();
  columnsModal.classList.remove('open');
  renderRows();
});

addColumnsPairButton.addEventListener('click', () => {
  columnsOptions.insertAdjacentHTML('beforeend', columnPairRowMarkup());
  refreshColumnsHint();
});

columnsModal.addEventListener('click', event => {
  if (event.target === columnsModal) columnsModal.classList.remove('open');
});

columnsOptions.addEventListener('click', event => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  if (target.classList.contains('remove-columns-pair-button')) {
    target.closest('.columns-row')?.remove();
    refreshColumnsHint();
  }
});

columnsOptions.addEventListener('change', event => {
  const target = event.target;
  if (!(target instanceof HTMLSelectElement)) return;
  const row = target.closest('.columns-row');
  if (!row) return;
  const file1Select = row.querySelector('.columns-file1-select');
  const file2Select = row.querySelector('.columns-file2-select');
  if (!(file1Select instanceof HTMLSelectElement) || !(file2Select instanceof HTMLSelectElement)) return;
  if (target === file1Select) {
    const shared = sharedFieldBySide('file1', file1Select.value);
    if (shared) file2Select.value = shared.file2_field || file2Select.value;
  }
  if (target === file2Select) {
    const shared = sharedFieldBySide('file2', file2Select.value);
    if (shared) file1Select.value = shared.file1_field || file1Select.value;
  }
});

backToVariablesButton.addEventListener('click', () => {
  pendingApprovedBlockingRules = selectedBlockingRulesFromEditor();
  renderPreviewContent(pendingSchemaMapping);
  showWorkflowPanel(previewModal);
});

addBlockingRuleButton.addEventListener('click', () => {
  markBlockingDirty();
  appendBlockingRuleRow();
});

blockingOptions.addEventListener('click', event => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  if (target.classList.contains('remove-rule-button')) {
    markBlockingDirty();
    const card = target.closest('.blocking-rule-card');
    if (!card) return;
    const previous = card.previousElementSibling;
    const next = card.nextElementSibling;
    if (previous && previous.classList.contains('blocking-or')) previous.remove();
    else if (next && next.classList.contains('blocking-or')) next.remove();
    card.remove();
    if (pendingBlockingPlan) {
      const remainingSqls = new Set(
        Array.from(blockingOptions.querySelectorAll('.blocking-sql-input')).map(t => t.value.trim())
      );
      pendingBlockingPlan.blocking_rules = (pendingBlockingPlan.blocking_rules || [])
        .filter(r => remainingSqls.has((r.sql || '').trim()));
    }
  }
});

comparatorOptions.addEventListener('change', event => {
  const select = event.target.closest('.comparator-select');
  if (!select) return;
  const row = select.closest('.choice-row');
  const thresholdWrap = row ? row.querySelector('.comparator-threshold-wrap') : null;
  const thresholdInput = row ? row.querySelector('.comparator-thresholds') : null;
  const thresholdHelp = thresholdWrap ? thresholdWrap.querySelector('small') : null;
  const thresholdLabel = thresholdWrap ? thresholdWrap.querySelector('span') : null;
  if (!thresholdInput || !thresholdWrap) return;
  if (comparatorSupportsThresholds(select.value)) {
    thresholdWrap.style.display = '';
    thresholdInput.placeholder = comparatorThresholdHint(select.value);
    if (thresholdHelp) thresholdHelp.textContent = comparatorThresholdHelp(select.value);
    if (thresholdLabel) thresholdLabel.textContent = 'Thresholds';
    const defaults = (((pendingComparisonPlan || {}).threshold_defaults || {})[select.value] || []);
    thresholdInput.value = defaults.join(', ');
  } else {
    thresholdWrap.style.display = 'none';
    thresholdInput.value = '';
  }
});

blockingOptions.addEventListener('change', event => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  markBlockingDirty();
  const card = target.closest('.blocking-rule-card');
  if (!card) return;
  refreshBlockingRuleCard(card);
});

regenerateBlockingButton.addEventListener('click', async () => {
  try {
    regenerateBlockingButton.disabled = true;
    blockingOptions.innerHTML = '<div class="empty">Regenerating blocking rules...</div>';
    markBlockingDirty();
    startProgress();
    pendingBlockingPlan = await analyzeBlockingRules(pendingSelectedFields);
    finishProgress('Blocking rules regenerated.');
    renderBlockingOptions(pendingBlockingPlan);
  } catch (error) {
    failProgress(error.message);
  } finally {
    regenerateBlockingButton.disabled = false;
  }
});

runWithBlockingButton.addEventListener('click', async () => {
  try {
    runWithBlockingButton.disabled = true;
    startProgress();
    await moveFromBlockingToComparators();
  } catch (error) {
    if (error.message === 'Linkage canceled') {
      setStatus(['Linkage canceled']);
      failProgress('Linkage canceled.');
      setWorkflowBusy(false);
    } else {
      failProgress(error.message);
    }
  } finally {
    setWorkflowBusy(false);
    runWithBlockingButton.disabled = false;
  }
});

blockingModal.addEventListener('click', event => {
  if (event.target === blockingModal) event.stopPropagation();
});

modeSelect.addEventListener('change', updateModeInputs);
[file1Input, file2Input].forEach(input => {
  input?.addEventListener('change', () => updateFilePickerName(input));
});
updateModeInputs();

body.addEventListener('change', event => {
  if (event.target.tagName !== 'SELECT') return;
  const pairId = event.target.dataset.pairId;
  const row = results.find(item => item.pair_id === pairId);
  if (!row) return;
  const previous = row.predicted_class;
  row.predicted_class = event.target.value;
  if (previous in resultsCounts) resultsCounts[previous] -= 1;
  if (row.predicted_class in resultsCounts) resultsCounts[row.predicted_class] += 1;
  const tableRow = event.target.closest('tr');
  if (tableRow) tableRow.className = statusRowClass(row.predicted_class);
  showResultsStatus();
});

let filterTimer = null;
// Labels changed on the page are saved before another page or ordering is loaded,
// so switching the filter or search never drops them.
async function saveLabelsThenFetch(page) {
  const data = await saveCurrentPageLabels();
  if (data) _applyResultsCounts(data);
  await fetchResultsPage(page);
}

searchBox.addEventListener('input', () => {
  clearTimeout(filterTimer);
  filterTimer = setTimeout(() => saveLabelsThenFetch(1).catch(error => failProgress(error.message)), 250);
});
classFilter.addEventListener('change', () => {
  saveLabelsThenFetch(1).catch(error => failProgress(error.message));
});

// Info icons sit inside toggle labels: a click shows the explanation instead of flipping the switch.
document.querySelectorAll('.info-tip').forEach(tip => tip.addEventListener('click', event => {
  event.preventDefault();
  tip.focus();
}));

// "Highlight differences" fades the fields that agree; remembered in this browser only.
function applyHighlightDifferences(on) {
  highlightDiffToggle.checked = on;
  resultsShell.classList.toggle('highlight-diff', on);
}
try { applyHighlightDifferences(localStorage.getItem('highlightDifferences') === '1'); } catch (error) { applyHighlightDifferences(false); }
highlightDiffToggle.addEventListener('change', () => {
  applyHighlightDifferences(highlightDiffToggle.checked);
  try { localStorage.setItem('highlightDifferences', highlightDiffToggle.checked ? '1' : '0'); } catch (error) { /* not remembered */ }
});

function _applyResultsCounts(data) {
  setResultsCounts(data);
}

prevPageButton.addEventListener('click', async () => {
  try {
    pageStatus.textContent = 'Saving page edits...';
    const data = await saveCurrentPageLabels();
    if (data) _applyResultsCounts(data);
    await fetchResultsPage(currentPage - 1);
  } catch (error) {
    failProgress(error.message);
  }
});
nextPageButton.addEventListener('click', async () => {
  try {
    pageStatus.textContent = 'Saving page edits...';
    const data = await saveCurrentPageLabels();
    if (data) _applyResultsCounts(data);
    await fetchResultsPage(currentPage + 1);
  } catch (error) {
    failProgress(error.message);
  }
});

saveButton.addEventListener('click', async () => {
  if (!currentJob) return;
  try {
    const data = await saveCurrentPageLabels();
    _applyResultsCounts(data);
  } catch (error) {
    failProgress(error.message);
  }
});

exportMenuButton.addEventListener('click', () => {
  if (!currentJob) return;
  exportModal.classList.add('open');
});

cancelExportButton.addEventListener('click', () => {
  exportModal.classList.remove('open');
});

exportModal.addEventListener('click', event => {
  if (event.target === exportModal) exportModal.classList.remove('open');
});

runExportButton.addEventListener('click', async () => {
  try {
    runExportButton.disabled = true;
    await runExport();
  } catch (error) {
    failProgress(error.message);
  } finally {
    runExportButton.disabled = false;
  }
});


settingsTabs.forEach(tab => {
  tab.addEventListener('click', () => activateSettingsTab(tab.dataset.tab || 'api'));
});

nonMatchThresholdInput.addEventListener('input', () => {
  if (Number(nonMatchThresholdInput.value) >= Number(matchThresholdInput.value)) {
    matchThresholdInput.value = Math.min(1, Number(nonMatchThresholdInput.value) + 0.01).toFixed(2);
  }
  syncThresholdWidgets();
});

matchThresholdInput.addEventListener('input', () => {
  if (Number(matchThresholdInput.value) <= Number(nonMatchThresholdInput.value)) {
    nonMatchThresholdInput.value = Math.max(0, Number(matchThresholdInput.value) - 0.01).toFixed(2);
  }
  syncThresholdWidgets();
});

settingsMenuButton.addEventListener('click', () => {
  settingsError.hidden = true;
  activateSettingsTab('api');
  loadSettings();
  settingsModal.classList.add('open');
});

stopJobButton.addEventListener('click', () => {
  cancelCurrentJob().catch(error => failProgress(error.message));
});

cancelSettingsButton.addEventListener('click', () => {
  settingsModal.classList.remove('open');
  loadSettings();
});

saveSettingsButton.addEventListener('click', async () => {
  try {
    saveSettings();
  } catch (error) {
    // Shown in the dialog, which stays open so the value can be corrected.
    settingsError.textContent = error.message || 'Could not save settings.';
    settingsError.hidden = false;
    return;
  }
  settingsError.hidden = true;
  settingsModal.classList.remove('open');
  try {
    if (currentJob && !document.body.classList.contains('busy')) {
      await reclassifyCurrentJobWithSettings();
      await fetchResultsPage(currentPage);
    }
    flashStatus(['Settings saved']);
  } catch (error) {
    failProgress(error.message || 'Could not save settings');
  }
});


settingsModal.addEventListener('click', event => {
  if (event.target === settingsModal) {
    settingsModal.classList.remove('open');
    loadSettings();
  }
});

setWorkflowBusy(false);
loadSettings();
activateSettingsTab('api');
setWizardStep('upload');

if (removeApiKeyButton) {
  removeApiKeyButton.addEventListener('click', () => {
    apiKeyInput.value = '';
    saveApiKey('').catch(error => setStatus([error.message]));
  });
}
