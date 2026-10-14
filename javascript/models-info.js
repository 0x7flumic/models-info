(function() {
    let cachedMetadata = { by_path: {}, by_filename: {} };
    let activePollers = {
        "Checkpoints": { isPolling: false, interval: null },
        "LoRA": { isPolling: false, interval: null }
    };

    let modalBackdrop = null;
    let displayNameInput = null;
    let baseModelDropdown = null;
    let baseModelCustomInput = null;
    let versionInput = null;
    let previewInput = null;
    let triggersInput = null;
    let urlInput = null;
    let currentModelFilepath = null;
    let currentModelMeta = null;

    let selectedFileObject = null;

    function ensureModalInjected() {
        if (gradioApp().querySelector('.models-info-modal-backdrop')) return;

        modalBackdrop = document.createElement('div');
        modalBackdrop.className = 'models-info-modal-backdrop';
        modalBackdrop.innerHTML = `
            <div class="models-info-modal">
                <h3>⚙️ Edit Model Metadata</h3>
                <div class="models-info-modal-group">
                    <label for="models-info-edit-display-name">Display Name (Card Title)</label>
                    <input type="text" id="models-info-edit-display-name" placeholder="Custom card title (leave empty for default)...">
                </div>
                <div class="models-info-modal-group">
                    <label for="models-info-edit-base">Base Model</label>
                    <select id="models-info-edit-base">
                        <option value="Unknown">Unknown</option>
                    </select>
                </div>
                <div class="models-info-modal-group" id="models-info-edit-base-custom-group">
                    <label for="models-info-edit-base-custom">Custom Base Model</label>
                    <input type="text" id="models-info-edit-base-custom" placeholder="Type custom base model name..." disabled>
                </div>
                <div class="models-info-modal-group">
                    <label for="models-info-edit-version">Version</label>
                    <input type="text" id="models-info-edit-version" placeholder="e.g. v1.0, main...">
                </div>
                <div class="models-info-modal-group">
                    <label for="models-info-edit-triggers">Trigger Words</label>
                    <input type="text" id="models-info-edit-triggers" placeholder="e.g. trigger1, trigger2 (comma separated)">
                </div>
                <div class="models-info-modal-group">
                    <label for="models-info-edit-url">External Source URL</label>
                    <input type="text" id="models-info-edit-url" placeholder="e.g. https://civitai.com/models/...">
                </div>
                <div class="models-info-modal-group">
                    <label for="models-info-edit-preview">Local Preview Image Path</label>
                    <div style="display: flex; gap: 8px; width: 100%;">
                        <input type="text" id="models-info-edit-preview" placeholder="C:\\path\\to\\preview_image.jpg" style="flex-grow: 1;">
                        <button id="models-info-edit-browse" class="models-info-modal-btn-cancel" style="padding: 8px 12px !important; margin: 0 !important; flex-shrink: 0;" title="Browse Local File">📂</button>
                    </div>
                    <input type="file" id="models-info-edit-file-input" accept="image/*" style="display: none;">
                </div>
                <div class="models-info-modal-actions">
                    <button class="models-info-modal-btn-cancel" id="models-info-edit-cancel">Cancel</button>
                    <button class="models-info-modal-btn-save" id="models-info-edit-save">Save</button>
                </div>
            </div>
        `;
        gradioApp().appendChild(modalBackdrop);

        displayNameInput = modalBackdrop.querySelector('#models-info-edit-display-name');
        baseModelDropdown = modalBackdrop.querySelector('#models-info-edit-base');
        baseModelCustomInput = modalBackdrop.querySelector('#models-info-edit-base-custom');
        versionInput = modalBackdrop.querySelector('#models-info-edit-version');
        previewInput = modalBackdrop.querySelector('#models-info-edit-preview');
        triggersInput = modalBackdrop.querySelector('#models-info-edit-triggers');
        urlInput = modalBackdrop.querySelector('#models-info-edit-url');

        fetchBaseModelsOptions();

        baseModelDropdown.addEventListener('change', (e) => {
            if (e.target.value === 'Other') {
                baseModelCustomInput.disabled = false;
                baseModelCustomInput.focus();
            } else {
                baseModelCustomInput.disabled = true;
                baseModelCustomInput.value = '';
            }
        });

        // Setup file browser button trigger
        const fileInput = modalBackdrop.querySelector('#models-info-edit-file-input');
        const browseBtn = modalBackdrop.querySelector('#models-info-edit-browse');

        browseBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            fileInput.click();
        });

        fileInput.addEventListener('change', (e) => {
            if (e.target.files && e.target.files.length > 0) {
                selectedFileObject = e.target.files[0];
                previewInput.value = selectedFileObject.name;
            }
        });

        previewInput.addEventListener('input', () => {
            selectedFileObject = null;
        });

        modalBackdrop.querySelector('#models-info-edit-cancel').addEventListener('click', () => {
            closeMetadataModal();
        });

        modalBackdrop.querySelector('#models-info-edit-save').addEventListener('click', () => {
            saveMetadataChanges();
        });
    }

    async function fetchBaseModelsOptions() {
        try {
            const response = await fetch('/models-info/api/base-models');
            if (response.ok) {
                const data = await response.json();
                const list = data.base_models || [];
                baseModelDropdown.innerHTML = '';
                list.forEach(m => {
                    const opt = document.createElement('option');
                    opt.value = m;
                    opt.textContent = m;
                    baseModelDropdown.appendChild(opt);
                });
                
                if (!list.includes("Unknown")) {
                    const unknownOpt = document.createElement('option');
                    unknownOpt.value = "Unknown";
                    unknownOpt.textContent = "Unknown";
                    baseModelDropdown.appendChild(unknownOpt);
                }
                const otherOpt = document.createElement('option');
                otherOpt.value = "Other";
                otherOpt.textContent = "Other (type custom)";
                baseModelDropdown.appendChild(otherOpt);
            }
        } catch (e) {
            console.error("[models-info] Failed to load base models options", e);
        }
    }

    async function openMetadataModal(filepath, meta) {
        ensureModalInjected();
        currentModelFilepath = filepath;

        const cardName = filepath ? filepath.split(/[\\/]/).pop() : '';
        const freshMeta = findModelMetadata(cardName, filepath) || meta || {};
        currentModelMeta = freshMeta;

        const currentDisplay = freshMeta.displayName || freshMeta.name || freshMeta.modelName || getDisplayModelName(cardName, freshMeta) || '';
        const currentBase = freshMeta.baseModel || 'Unknown';
        const currentVersion = freshMeta.versionName || 'Unknown';
        const currentTriggers = (freshMeta.trainedWords || []).join(', ');
        const currentUrl = freshMeta.externalUrl || '';

        displayNameInput.value = currentDisplay;
        baseModelCustomInput.value = '';
        versionInput.value = (currentVersion === 'Unknown') ? '' : currentVersion;
        previewInput.value = '';
        selectedFileObject = null;
        triggersInput.value = currentTriggers;
        urlInput.value = currentUrl;

        let found = false;
        Array.from(baseModelDropdown.options).forEach(opt => {
            if (opt.value === currentBase) {
                baseModelDropdown.value = currentBase;
                found = true;
            }
        });

        if (!found && currentBase && currentBase !== 'Unknown') {
            baseModelDropdown.value = 'Other';
            baseModelCustomInput.value = currentBase;
            baseModelCustomInput.disabled = false;
        } else {
            if (!currentBase || currentBase === 'Unknown') {
                baseModelDropdown.value = 'Unknown';
            }
            baseModelCustomInput.disabled = true;
        }

        modalBackdrop.classList.add('show');

        // Fetch live sidecar data directly from disk to guarantee freshest values
        try {
            const resp = await fetch(`/models-info/api/model-metadata?filepath=${encodeURIComponent(filepath)}`);
            if (resp.ok) {
                const liveData = await resp.json();
                if (liveData && Object.keys(liveData).length > 0) {
                    currentModelMeta = Object.assign(freshMeta, liveData);
                    const liveDisplay = liveData.displayName || liveData.name || liveData.modelName || getDisplayModelName(cardName, liveData) || '';
                    if (liveDisplay) {
                        displayNameInput.value = liveDisplay;
                    }
                    if (liveData.baseModel && liveData.baseModel !== 'Unknown') {
                        let f = false;
                        Array.from(baseModelDropdown.options).forEach(opt => {
                            if (opt.value === liveData.baseModel) {
                                baseModelDropdown.value = liveData.baseModel;
                                f = true;
                            }
                        });
                        if (!f) {
                            baseModelDropdown.value = 'Other';
                            baseModelCustomInput.value = liveData.baseModel;
                            baseModelCustomInput.disabled = false;
                        }
                    }
                    if (liveData.versionName && liveData.versionName !== 'Unknown') {
                        versionInput.value = liveData.versionName;
                    }
                    if (liveData.trainedWords && liveData.trainedWords.length > 0) {
                        triggersInput.value = liveData.trainedWords.join(', ');
                    }
                    if (liveData.externalUrl) {
                        urlInput.value = liveData.externalUrl;
                    }
                }
            }
        } catch (e) {
            console.warn('[models-info] Error fetching live model metadata:', e);
        }
    }

    function closeMetadataModal() {
        if (modalBackdrop) {
            modalBackdrop.classList.remove('show');
        }
    }

    async function saveMetadataChanges() {
        let baseVal = baseModelDropdown.value;
        if (baseVal === 'Other') {
            baseVal = baseModelCustomInput.value.trim() || 'Unknown';
        }

        const saveBtn = modalBackdrop.querySelector('#models-info-edit-save');
        saveBtn.disabled = true;
        saveBtn.textContent = 'Saving...';

        try {
            const formData = new FormData();
            formData.append('filepath', currentModelFilepath);
            formData.append('display_name', displayNameInput.value.trim());
            formData.append('base_model', baseVal);
            formData.append('version_name', versionInput.value.trim() || 'Unknown');
            formData.append('trained_words', triggersInput.value.trim());
            formData.append('external_url', urlInput.value.trim());
            
            if (selectedFileObject) {
                formData.append('preview_file', selectedFileObject);
            } else {
                formData.append('preview_local_path', previewInput.value.trim());
            }

            const response = await fetch('/models-info/api/edit-metadata', {
                method: 'POST',
                body: formData
            });

            const result = await response.json();
            if (response.ok && result.status === 'success') {
                if (currentModelMeta) {
                    currentModelMeta.displayName = displayNameInput.value.trim();
                }
                closeMetadataModal();                
                await triggerWebUIRefreshAndSync();
            } else {
                alert(`Failed to save metadata: ${result.detail || 'Unknown error'}`);
            }
        } catch (e) {
            alert(`Error: ${e.message}`);
        } finally {
            saveBtn.disabled = false;
            saveBtn.textContent = 'Save';
        }
    }

    function findModelMetadata(cardName, filepath) {
        if (!cachedMetadata || !cachedMetadata.by_filename) return null;

        const normFilepath = filepath ? filepath.replace(/\\/g, '/') : '';
        const baseName = filepath ? filepath.split(/[\\/]/).pop() : cardName;
        const baseNameNoExt = baseName ? baseName.replace(/\.[^/.]+$/, '') : '';
        const cardNameNoExt = cardName ? cardName.replace(/\.[^/.]+$/, '') : '';

        return cachedMetadata.by_filename[cardName]
            || cachedMetadata.by_filename[cardNameNoExt]
            || cachedMetadata.by_filename[cardName.toLowerCase()]
            || cachedMetadata.by_filename[cardNameNoExt.toLowerCase()]
            || cachedMetadata.by_filename[baseName]
            || cachedMetadata.by_filename[baseNameNoExt]
            || cachedMetadata.by_filename[baseName.toLowerCase()]
            || cachedMetadata.by_filename[baseNameNoExt.toLowerCase()]
            || cachedMetadata.by_path[filepath]
            || cachedMetadata.by_path[normFilepath]
            || cachedMetadata.by_path[normFilepath.replace(/\.[^/.]+$/, '')]
            || null;
    }

    // Load all metadata from API once on page load or scan finish
    async function refreshMetadata() {
        try {
            const response = await fetch('/models-info/api/metadata-all');
            if (response.ok) {
                cachedMetadata = await response.json();

                // Sync card size setting directly from API response
                if (cachedMetadata.settings && cachedMetadata.settings.enforce_card_size === false) {
                    document.body.classList.add('models-info-natural-size');
                } else {
                    document.body.classList.remove('models-info-natural-size');
                }

                // Repopulate dropdown lists dynamically for all active toolbars
                gradioApp().querySelectorAll('.models-info-filter').forEach(selectEl => {
                    const controlsDiv = selectEl.closest('[id$="_controls"]');
                    const id = controlsDiv ? (controlsDiv.id || "") : "";
                    let tabType = null;
                    if (id.includes('checkpoints')) {
                        tabType = "Checkpoints";
                    } else if (id.includes('lora')) {
                        tabType = "LoRA";
                    }
                    populateDynamicFilters(selectEl, tabType);
                });

                // Re-apply active filters to ensure cards reflect current selection
                gradioApp().querySelectorAll('.models-info-filter').forEach(selectEl => {
                    if (selectEl.value && selectEl.value !== 'All') {
                        selectEl.dispatchEvent(new Event('change'));
                    } else {
                        gradioApp().querySelectorAll('.card.models-info-filtered-out').forEach(c => c.classList.remove('models-info-filtered-out'));
                    }
                });
            }
        } catch (e) {
            console.error("[models-info] Failed to load metadata from backend", e);
        }
    }

    // Populate dropdown options dynamically from the loaded model metadata
    function populateDynamicFilters(selectElement, tabType) {
        const uniqueBases = new Set();
        
        // Scan cachedMetadata to collect all unique base model tags for this specific tabType
        Object.entries(cachedMetadata.by_path).forEach(([path, meta]) => {
            if (!meta.baseModel) return;
            
            const normalizedPath = path.toLowerCase().replace(/\\/g, '/');
            const metaType = (meta.type || '').toLowerCase();
            
            let baseText = meta.baseModel.trim();
            
            if (tabType === "Checkpoints") {
                if (normalizedPath.includes('stable-diffusion') || metaType.includes('checkpoint')) {
                    uniqueBases.add(baseText);
                }
            } else if (tabType === "LoRA") {
                if (normalizedPath.includes('lora') || metaType.includes('lora')) {
                    uniqueBases.add(baseText);
                }
            } else {
                uniqueBases.add(baseText);
            }
        });

        const sortedBases = Array.from(uniqueBases).sort();
        
        // Keep active selection value if any
        const prevValue = selectElement.value || "All";
        
        selectElement.innerHTML = '<option value="All">All Base Models</option>';
        sortedBases.forEach(baseName => {
            if (baseName !== "Unknown") {
                const opt = document.createElement('option');
                opt.value = baseName;
                opt.textContent = baseName;
                selectElement.appendChild(opt);
            }
        });

        // Always add "Unknown" as a filter option so users can group unscanned/unmatched models
        const unknownOpt = document.createElement('option');
        unknownOpt.value = "Unknown";
        unknownOpt.textContent = "Unknown";
        selectElement.appendChild(unknownOpt);

        // Restore active selection if still available
        if (uniqueBases.has(prevValue) || prevValue === "All" || prevValue === "Unknown") {
            selectElement.value = prevValue;
        }
    }

    // Helper: Find active prompt textarea in the currently selected tab
    // Works seamlessly with modern WebUI Forge structures
    function getActivePromptTextarea() {
        const isImg2Img = !!gradioApp().querySelector('#tabs button.selected, #tabs button.active')?.textContent.includes('img2img');
        const id = isImg2Img ? '#img2img_prompt textarea' : '#txt2img_prompt textarea';
        return gradioApp().querySelector(id);
    }

    // Helper: Trigger event listener on prompt textarea to notify Gradio/React of change
    function triggerInputChange(element) {
        const event = new Event('input', { bubbles: true });
        element.dispatchEvent(event);
    }

    // Helper: Trigger WebUI extra network refresh and synchronize filters
    async function triggerWebUIRefreshAndSync(filterSelector) {
        // Find refresh buttons across standard WebUI and WebUI Forge structures
        const selectors = filterSelector ? [filterSelector] : [
            '[id$="_extra_refresh_internal"]',
            '[id$="_extra_refresh"]',
            '.extra-network-refresh',
            'button[id*="refresh"]'
        ];
        
        let clicked = false;
        for (const sel of selectors) {
            const btns = gradioApp().querySelectorAll(sel);
            if (btns && btns.length > 0) {
                btns.forEach(btn => {
                    if (typeof btn.click === 'function') {
                        btn.click();
                        clicked = true;
                    }
                });
                if (filterSelector) break;
            }
        }

        await refreshMetadata();
        
        // Staged re-processing to catch cards as Gradio regenerates them
        debouncedProcessAllGrids(100);
        setTimeout(() => processAllGrids(), 350);
        setTimeout(() => processAllGrids(), 800);
        setTimeout(() => processAllGrids(), 1500);
    }

    // Dispatch rename model to backend
    async function renameModel(filepath, oldFilename) {
        const newName = prompt(`Enter new filename for ${oldFilename} (without extension):`);
        if (!newName || newName.trim() === '') return;

        try {
            const response = await fetch('/models-info/api/rename', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ filepath: filepath, new_name: newName.trim() })
            });

            const result = await response.json();
            if (response.ok && result.status === 'success') {
                await triggerWebUIRefreshAndSync();
            } else {
                alert(`Failed to rename: ${result.detail || 'Unknown error'}`);
            }
        } catch (e) {
            alert(`Error: ${e.message}`);
        }
    }

    // Dispatch delete model to backend
    async function deleteModel(filepath, filename) {
        if (!confirm(`Permanently delete ${filename}?\n\nThis action will delete the model and all associated preview images/metadata. It cannot be undone.`)) {
            return;
        }

        try {
            const response = await fetch('/models-info/api/delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ filepath: filepath })
            });

            const result = await response.json();
            if (response.ok && result.status === 'success') {
                await triggerWebUIRefreshAndSync();
            } else {
                alert(`Failed to delete: ${result.detail || 'Unknown error'}`);
            }
        } catch (e) {
            alert(`Error: ${e.message}`);
        }
    }

    // Apply local client-side card filtering based on the selected base model option and favorites toggle
    function applyLocalCardFilter(tabContainer, baseFilter, favoritesOnly = false) {
        const cards = tabContainer.querySelectorAll('.card');
        const isAll = (!baseFilter || baseFilter === 'All');

        // Fast-path: if no filters active, guarantee all cards are visible
        if (isAll && !favoritesOnly) {
            cards.forEach(card => card.classList.remove('models-info-filtered-out'));
            return;
        }

        cards.forEach(card => {
            const nameSpan = card.querySelector('.name');
            const cardName = card.dataset?.originalName || nameSpan?.textContent?.trim() || card.dataset?.name || "";
            const filepath = card.querySelector('.copy-path-button')?.getAttribute('data-clipboard-text') || card.dataset?.path || cardName;
            
            const meta = findModelMetadata(cardName, filepath);

            // 1. Favorites filter check
            if (favoritesOnly && (!meta || meta.favorite !== true)) {
                card.classList.add('models-info-filtered-out');
                return;
            }

            // 2. Base model filter check
            if (isAll) {
                card.classList.remove('models-info-filtered-out');
                return;
            }

            if (!meta) {
                // If metadata hasn't loaded or isn't available, only show when filtering for Unknown
                const hide = (baseFilter !== 'Unknown');
                card.classList.toggle('models-info-filtered-out', hide);
                return;
            }

            const base = (meta.baseModel || 'Unknown').toLowerCase().trim();
            const filterLower = baseFilter.toLowerCase().trim();

            let matches = (base === filterLower);
            if (!matches) {
                // If filter is general (e.g. "Anima" without parentheses), allow matching "Anima (2B)"
                // But if filter is specific (e.g. "Anima (2B)"), require exact equality
                if (!filterLower.includes('(')) {
                    matches = base.startsWith(filterLower + ' ') || base.startsWith(filterLower + '(');
                }
            }
            card.classList.toggle('models-info-filtered-out', !matches);
        });
    }

    // Helper: Find corresponding pane for controls container
    function getPaneForControls(controlsContainer) {
        const paneId = controlsContainer.id ? controlsContainer.id.replace('_controls', '_pane') : null;
        let pane = paneId ? gradioApp().getElementById(paneId) : null;
        if (!pane) {
            pane = controlsContainer.closest('.extra-network-pane') || controlsContainer.parentElement?.querySelector('.extra-network-cards, .extra-networks-grid')?.closest('[id$="_pane"]');
        }
        if (!pane) {
            pane = controlsContainer.parentElement;
        }
        return pane;
    }

    // Injected Header Toolbar (Scan & Dynamic Filter)
    function injectHeaderToolbar(controlsContainer, tabType) {
        if (controlsContainer.querySelector('.models-info-toolbar')) return;

        const toolbar = document.createElement('div');
        toolbar.className = 'models-info-toolbar';
        toolbar.innerHTML = `
            <select class="models-info-filter" title="Filter by Base Model">
                <option value="All">All Base Models</option>
            </select>
            <button class="models-info-fav-filter-btn" title="Toggle Favorites Only">⭐ Favorites</button>
            <button class="models-info-scan-btn" title="Scan models (Shift+Click to force rescan all)">🔍 Scan</button>
            <div class="models-info-inline-progress" style="display: none;">
                <div class="models-info-inline-progress-fill" style="width: 0%"></div>
                <span class="models-info-inline-progress-text">0%</span>
            </div>
        `;

        const selectEl = toolbar.querySelector('.models-info-filter');
        populateDynamicFilters(selectEl, tabType);

        // Bind favorites toggle button
        const favBtn = toolbar.querySelector('.models-info-fav-filter-btn');
        favBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            const isFavActive = toolbar.dataset.favOnly === "true";
            const nextState = !isFavActive;
            toolbar.dataset.favOnly = nextState ? "true" : "false";
            favBtn.classList.toggle('active', nextState);
            const pane = getPaneForControls(controlsContainer);
            if (pane) {
                applyLocalCardFilter(pane, selectEl.value, nextState);
            }
        });

        // Bind local filtering selection
        selectEl.addEventListener('change', (e) => {
            const pane = getPaneForControls(controlsContainer);
            if (pane) {
                const favActive = toolbar.dataset.favOnly === "true";
                applyLocalCardFilter(pane, e.target.value, favActive);
            }
        });

        // Bind scanning click (supports Shift+Click for force rescan)
        const scanBtn = toolbar.querySelector('.models-info-scan-btn');
        scanBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            const forceAll = e.shiftKey === true;
            triggerTabScan(tabType, toolbar, forceAll);
        });

        // Insert toolbar before the native Search input field
        const searchInput = controlsContainer.querySelector('.extra-network-control--search')
            || controlsContainer.querySelector('input[type="text"]')
            || controlsContainer.querySelector('.search');
        if (searchInput) {
            controlsContainer.insertBefore(toolbar, searchInput);
        } else {
            controlsContainer.appendChild(toolbar);
        }
    }

    // Dispatch background scan or cancel
    async function triggerTabScan(tabType, toolbarEl, forceAll = false) {
        const isRunning = toolbarEl.dataset.running === "true";
        const endpoint = isRunning ? '/models-info/api/cancel' : '/models-info/api/scan';
        
        let scanMissingOnly = true;
        if (forceAll) {
            scanMissingOnly = false;
        } else if (cachedMetadata && cachedMetadata.settings && cachedMetadata.settings.rescan_all === true) {
            scanMissingOnly = false;
        }

        const payload = isRunning ? { model_type: tabType } : {
            model_type: tabType,
            scan_missing_only: scanMissingOnly
        };

        try {
            const response = await fetch(endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            if (response.ok) {
                if (!isRunning) {
                    startIsolatedPolling(tabType, toolbarEl);
                } else {
                    stopIsolatedPolling(tabType, toolbarEl);
                }
            }
        } catch (e) {
            console.error(`[models-info] Failed to trigger scan for ${tabType}`, e);
        }
    }

    // Real-time inline progress polling per tab
    function startIsolatedPolling(tabType, toolbarEl) {
        const poller = activePollers[tabType];
        if (poller.isPolling) return;

        const scanBtn = toolbarEl.querySelector('.models-info-scan-btn');
        const progressEl = toolbarEl.querySelector('.models-info-inline-progress');
        const fillEl = toolbarEl.querySelector('.models-info-inline-progress-fill');
        const textEl = toolbarEl.querySelector('.models-info-inline-progress-text');

        scanBtn.textContent = '⏹ Cancel';
        scanBtn.style.background = '#dc2626'; // red Cancel button
        toolbarEl.dataset.running = "true";
        progressEl.style.display = 'inline-flex';

        poller.isPolling = true;
        poller.interval = setInterval(async () => {
            try {
                const response = await fetch(`/models-info/api/progress/${tabType}`);
                if (response.ok) {
                    const data = await response.json();
                    
                    fillEl.style.width = `${data.percent}%`;
                    textEl.textContent = `${data.percent}%`;

                    if (!data.is_running) {
                        stopIsolatedPolling(tabType, toolbarEl);
                    }
                }
            } catch (e) {
                console.error(`[models-info] Error polling progress for ${tabType}`, e);
            }
        }, 500);
    }

    // Stop real-time polling
    function stopIsolatedPolling(tabType, toolbarEl) {
        const poller = activePollers[tabType];
        if (poller.interval) {
            clearInterval(poller.interval);
        }
        poller.isPolling = false;
        poller.interval = null;

        const scanBtn = toolbarEl.querySelector('.models-info-scan-btn');
        const progressEl = toolbarEl.querySelector('.models-info-inline-progress');
        
        scanBtn.textContent = '🔍 Scan';
        scanBtn.style.background = ''; // restore theme default
        toolbarEl.dataset.running = "false";
        progressEl.style.display = 'none';

        const searchPattern = tabType.toLowerCase();
        // Reliable selector pattern for active tab refresh in WebUI & Forge
        triggerWebUIRefreshAndSync(`[id*="${searchPattern}"][id*="refresh"], [id*="${searchPattern}"] .extra-network-refresh`);
    }

    // Check if a card is located inside the Checkpoints or LoRA tabs to restrict modification
    function isCardInCheckpointOrLora(card) {
        const pane = card.closest('[id$="_pane"]');
        if (!pane) return false;
        const id = pane.id.toLowerCase();
        return id.includes('checkpoints') || id.includes('lora');
    }

    // Strip version suffix from card name to display clean model name (e.g. "Cyber Realistic_V4.2" -> "Cyber Realistic")
    function getDisplayModelName(cardName, meta) {
        if (!cardName) return "";

        // 0. Custom Display Name override
        if (meta && meta.displayName && String(meta.displayName).trim() !== '') {
            return String(meta.displayName).trim();
        }

        let clean = cardName.replace(/\.(safetensors|ckpt|pt|bin)$/i, '').trim();

        // 1. If metadata has versionName, attempt exact or sanitized match stripping
        if (meta && meta.versionName && meta.versionName !== 'Unknown') {
            const vNames = [meta.versionName.trim()];
            const sanitizedV = meta.versionName.replace(/[^a-zA-Z0-9\s._-]/g, '').trim();
            if (sanitizedV && sanitizedV !== vNames[0]) {
                vNames.push(sanitizedV);
            }

            for (const vn of vNames) {
                const escV = vn.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/[\s_]+/g, '[\\s_]+');
                const vRegex = new RegExp('([_\\-\\s])' + escV + '(\\s*-\\s*Duplicate\\s*\\d+)?$', 'i');
                if (vRegex.test(clean)) {
                    return clean.replace(vRegex, (match, sep, dup) => (dup ? (' ' + dup.trim()) : '')).trim();
                }
            }
        }

        // 2. Generic fallback for version patterns like _V4.2, _v1.0, _1.5, etc.
        clean = clean.replace(/([_\\-\\s])([vV]?\d+[\w.-]*)(\s*-\\s*Duplicate\\s*\\d+)?$/i, (match, sep, ver, dup) => {
            return dup ? (' ' + dup.trim()) : '';
        }).trim();

        return clean;
    }

    // Inject base model badge and direct horizontal actions row above the model name banner
    function customizeCard(card, cardName, filepath) {
        if (!isCardInCheckpointOrLora(card)) return;

        const nameSpan = card.querySelector('.name');
        if (nameSpan && !card.dataset.originalName) {
            card.dataset.originalName = nameSpan.textContent.trim() || cardName;
        }

        card.classList.add('models-info-custom-card');

        const lookupName = card.dataset.originalName || cardName;
        let meta = findModelMetadata(lookupName, filepath);
        if (!meta) {
            meta = {
                baseModel: "Unknown",
                versionName: "Unknown",
                externalUrl: "",
                trainedWords: [],
                isUnknown: true
            };
        } else {
            if (!meta.baseModel || String(meta.baseModel).trim() === '') {
                meta.baseModel = "Unknown";
            }
            if (!meta.versionName || String(meta.versionName).trim() === '') {
                meta.versionName = "Unknown";
            }
        }

        // 3. Inject Version Badge & Update Card Display Name (without version)
        if (nameSpan) {
            const displayName = getDisplayModelName(card.dataset.originalName || cardName, meta);
            if (displayName && nameSpan.textContent !== displayName) {
                nameSpan.textContent = displayName;
                nameSpan.title = card.dataset.originalName || cardName;
            }
        }

        if (meta.versionName && nameSpan) {
            let actionsContainer = nameSpan.parentElement;
            if (actionsContainer) {
                let versionBadge = actionsContainer.querySelector('.models-info-version-badge');
                if (!versionBadge) {
                    versionBadge = document.createElement('div');
                    versionBadge.className = 'models-info-version-badge';
                    nameSpan.after(versionBadge);
                }
                versionBadge.textContent = meta.versionName;
            }
        }

        // 1. Badges Container (top-left)
        let badgesContainer = card.querySelector('.models-info-badges-container');
        if (!badgesContainer) {
            badgesContainer = document.createElement('div');
            badgesContainer.className = 'models-info-badges-container';
            card.style.position = 'relative';
            const oldBadge = card.querySelector(':scope > .models-info-base-badge');
            if (oldBadge) {
                oldBadge.remove();
            }
            card.appendChild(badgesContainer);
        }

        if (meta.baseModel && !badgesContainer.querySelector('.models-info-base-badge')) {
            const badge = document.createElement('div');
            badge.className = 'models-info-base-badge';
            
            const baseText = meta.baseModel.trim();
            badge.textContent = baseText;
            
            const base = baseText.toLowerCase();
            if (base === 'unknown') {
                badge.style.backgroundColor = '#4b5563'; // Slate gray for Unknown
                badge.style.color = '#ffffff';
            } else {
                // Generate HSL color based on base name hash
                let hash = 0;
                for (let i = 0; i < base.length; i++) {
                     hash = base.charCodeAt(i) + ((hash << 5) - hash);
                }
                const hue = Math.abs(hash % 360);
                
                // Scale lightness based on theme
                const isDark = document.body.classList.contains('dark') || document.querySelector('.dark') !== null;
                const lightness = isDark ? 35 : 75;
                badge.style.color = isDark ? '#ffffff' : '#1e293b';
                badge.style.backgroundColor = `hsl(${hue}, 70%, ${lightness}%)`;
            }

            badgesContainer.appendChild(badge);
        }

        // Top-left Favorite Star Badge
        let favBadge = badgesContainer.querySelector('.models-info-fav-badge');
        if (meta.favorite === true) {
            if (!favBadge) {
                favBadge = document.createElement('div');
                favBadge.className = 'models-info-fav-badge';
                favBadge.textContent = '★';
                favBadge.title = 'Favorited';
                badgesContainer.appendChild(favBadge);
            }
        } else if (favBadge) {
            favBadge.remove();
        }

        // 2. Actions Row
        let actionsRow = card.querySelector('.models-info-actions-row');
        if (!actionsRow) {
            actionsRow = document.createElement('div');
            actionsRow.className = 'models-info-actions-row';

            // Button 1: Toggle Favorite
            const favBtn = document.createElement('button');
            favBtn.className = 'models-info-card-btn models-info-fav-card-btn';
            const isFav = meta.favorite === true;
            favBtn.innerHTML = isFav ? '★' : '☆';
            favBtn.title = isFav ? 'Remove from Favorites' : 'Add to Favorites';
            if (isFav) favBtn.classList.add('active');
            favBtn.onclick = async (e) => {
                e.stopPropagation();
                try {
                    const res = await fetch('/models-info/api/toggle-favorite', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ filepath: filepath })
                    });
                    const data = await res.json();
                    if (res.ok && data.status === 'success') {
                        const newFav = data.favorite === true;
                        meta.favorite = newFav;
                        favBtn.innerHTML = newFav ? '★' : '☆';
                        favBtn.title = newFav ? 'Remove from Favorites' : 'Add to Favorites';
                        favBtn.classList.toggle('active', newFav);

                        let fb = badgesContainer.querySelector('.models-info-fav-badge');
                        if (newFav) {
                            if (!fb) {
                                fb = document.createElement('div');
                                fb.className = 'models-info-fav-badge';
                                fb.textContent = '★';
                                fb.title = 'Favorited';
                                badgesContainer.appendChild(fb);
                            }
                        } else if (fb) {
                            fb.remove();
                        }
                    }
                } catch (err) {
                    console.error('[models-info] Error toggling favorite:', err);
                }
            };
            actionsRow.appendChild(favBtn);

            // Button 2: Open Link
            const openBtn = document.createElement('button');
            openBtn.className = 'models-info-card-btn';
            openBtn.innerHTML = '🌐';
            openBtn.title = 'Open Source Page';
            if (!meta.externalUrl) openBtn.disabled = true;
            openBtn.onclick = (e) => {
                e.stopPropagation();
                if (meta.externalUrl) window.open(meta.externalUrl, '_blank');
            };
            actionsRow.appendChild(openBtn);

            // Button 3: Inject Triggers
            const triggerBtn = document.createElement('button');
            triggerBtn.className = 'models-info-card-btn';
            triggerBtn.innerHTML = '🏷️';
            triggerBtn.title = 'Inject Trigger Words';
            if (!meta.trainedWords || meta.trainedWords.length === 0) triggerBtn.disabled = true;
            triggerBtn.onclick = (e) => {
                e.stopPropagation();
                const textarea = getActivePromptTextarea();
                if (textarea && meta.trainedWords) {
                    const triggers = meta.trainedWords.join(', ');
                    const val = textarea.value.trim();
                    textarea.value = val ? `${val}, ${triggers}` : triggers;
                    triggerInputChange(textarea);
                }
            };
            actionsRow.appendChild(triggerBtn);

            // Button 4: Edit Metadata
            const editBtn = document.createElement('button');
            editBtn.className = 'models-info-card-btn';
            editBtn.innerHTML = '⚙️';
            editBtn.title = 'Edit Metadata & Preview';
            editBtn.onclick = (e) => {
                e.stopPropagation();
                const fresh = findModelMetadata(card.dataset?.originalName || cardName, filepath);
                openMetadataModal(filepath, fresh || meta);
            };
            actionsRow.appendChild(editBtn);

            // Button 5: Rename
            const renameBtn = document.createElement('button');
            renameBtn.className = 'models-info-card-btn';
            renameBtn.innerHTML = '✏️';
            renameBtn.title = 'Rename Model';
            renameBtn.onclick = (e) => {
                e.stopPropagation();
                renameModel(filepath, card.dataset?.originalName || cardName);
            };
            actionsRow.appendChild(renameBtn);

            // Button 6: Delete
            const deleteBtn = document.createElement('button');
            deleteBtn.className = 'models-info-card-btn';
            deleteBtn.innerHTML = '🗑️';
            deleteBtn.title = 'Permanently Delete Model';
            deleteBtn.onclick = (e) => {
                e.stopPropagation();
                deleteModel(filepath, card.dataset?.originalName || cardName);
            };
            actionsRow.appendChild(deleteBtn);

            card.appendChild(actionsRow);
        } else {
            const favBtn = actionsRow.querySelector('.models-info-fav-card-btn');
            if (favBtn) {
                const isFav = meta.favorite === true;
                favBtn.innerHTML = isFav ? '★' : '☆';
                favBtn.title = isFav ? 'Remove from Favorites' : 'Add to Favorites';
                favBtn.classList.toggle('active', isFav);
            }
        }
    }

    // Process grids to inject headers and customize cards
    function processAllGrids() {
        // 1. Find and inject header toolbars
        const controls = gradioApp().querySelectorAll('[id$="_controls"]');
        controls.forEach(controlEl => {
            const id = controlEl.id || "";
            let tabType = null;
            if (id.includes('checkpoints')) {
                tabType = "Checkpoints";
            } else if (id.includes('lora')) {
                tabType = "LoRA";
            }
            
            if (tabType) {
                injectHeaderToolbar(controlEl, tabType);
            }
        });

        // 2. Customize cards in checkpoints and lora
        const containers = gradioApp().querySelectorAll('.extra-network-cards, .extra-networks-grid');
        containers.forEach(container => {
            const cards = container.querySelectorAll('.card');
            cards.forEach(card => {
                const nameSpan = card.querySelector('.name');
                const cardName = card.dataset?.originalName || nameSpan?.textContent?.trim() || card.dataset?.name || "";
                const filepath = card.querySelector('.copy-path-button')?.getAttribute('data-clipboard-text') || card.dataset?.path || cardName;
                
                if (cardName) {
                    customizeCard(card, cardName, filepath);
                }
            });
        });

        // 3. Re-apply active toolbar filters to all panes to preserve selection across DOM reloads
        controls.forEach(controlEl => {
            const toolbar = controlEl.querySelector('.models-info-toolbar');
            const selectEl = controlEl.querySelector('.models-info-filter');
            const favActive = toolbar?.dataset?.favOnly === "true";
            if ((selectEl && selectEl.value && selectEl.value !== 'All') || favActive) {
                const pane = getPaneForControls(controlEl);
                if (pane) {
                    applyLocalCardFilter(pane, selectEl ? selectEl.value : 'All', favActive);
                }
            }
        });
    }

    let processDebounceTimer = null;
    function debouncedProcessAllGrids(delay = 150) {
        if (processDebounceTimer) {
            clearTimeout(processDebounceTimer);
        }
        processDebounceTimer = setTimeout(() => {
            processAllGrids();
            processDebounceTimer = null;
        }, delay);
    }

    // Mutation Observer to scan DOM changes dynamically with debouncing and relevance checks
    function initCardObserver() {
        const observer = new MutationObserver((mutations) => {
            let relevant = false;
            for (let i = 0; i < mutations.length; i++) {
                const target = mutations[i].target;
                if (!target) continue;

                // Ignore mutations on models-info's own injected UI elements
                if (target.closest && (
                    target.closest('.models-info-actions-row') ||
                    target.closest('.models-info-toolbar') ||
                    target.closest('.models-info-inline-progress') ||
                    target.closest('.models-info-modal-backdrop')
                )) {
                    continue;
                }

                // Check added nodes for new cards or extra network containers
                const added = mutations[i].addedNodes;
                for (let j = 0; j < added.length; j++) {
                    const node = added[j];
                    if (node.nodeType === 1) {
                        // Skip self-injected classes
                        if (node.classList?.contains('models-info-actions-row') ||
                            node.classList?.contains('models-info-base-badge') ||
                            node.classList?.contains('models-info-version-badge') ||
                            node.classList?.contains('models-info-toolbar')) {
                            continue;
                        }
                        if (node.classList?.contains('card') && !node.classList?.contains('models-info-custom-card')) {
                            relevant = true;
                            break;
                        }
                        if (node.classList?.contains('extra-network-cards') || node.classList?.contains('extra-networks-grid')) {
                            relevant = true;
                            break;
                        }
                        if (node.querySelector?.('.card:not(.models-info-custom-card)')) {
                            relevant = true;
                            break;
                        }
                    }
                }
                if (relevant) break;
            }

            if (relevant) {
                debouncedProcessAllGrids(150);
            }
        });

        const app = gradioApp();
        if (app) {
            observer.observe(app, { childList: true, subtree: true });
        }
    }

    let initialized = false;
    async function init() {
        if (initialized) return;
        initialized = true;
        await refreshMetadata();
        processAllGrids();
        initCardObserver();
    }

    // Initialize all components when WebUI finishes loading UI
    if (typeof onUiLoaded === "function") {
        onUiLoaded(init);
    } else {
        document.addEventListener("DOMContentLoaded", init);
    }

    // Fallback immediate initialization if onUiLoaded already fired
    setTimeout(init, 1200);

})();
