document.addEventListener('DOMContentLoaded', function() {
    const selectors = document.querySelectorAll('.smart-model-selector');

    selectors.forEach(container => {
        const name = container.id.replace('selector-', '');
        const allModels = JSON.parse(container.dataset.allModels);
        const appSelect = document.getElementById(`app-select-${name}`);
        const modelSelect = document.getElementById(`model-select-${name}`);
        const addButton = document.getElementById(`add-model-${name}`);
        const pillsContainer = document.getElementById(`selected-models-${name}`);
        const hiddenInput = document.getElementById(`hidden-input-${name}`);

        // Populate apps
        Object.keys(allModels).sort().forEach(appLabel => {
            const option = document.createElement('option');
            option.value = appLabel;
            option.textContent = allModels[appLabel].label;
            appSelect.appendChild(option);
        });

        appSelect.addEventListener('change', function() {
            const appLabel = this.value;
            modelSelect.innerHTML = '<option value="">-- Select Model --</option>';

            if (appLabel && allModels[appLabel]) {
                allModels[appLabel].models.forEach(model => {
                    const option = document.createElement('option');
                    option.value = model.value;
                    option.textContent = model.label;
                    modelSelect.appendChild(option);
                });
                modelSelect.disabled = false;
            } else {
                modelSelect.disabled = true;
            }
        });

        addButton.addEventListener('click', function() {
            const modelValue = modelSelect.value;
            if (!modelValue) return;

            // Check if already added
            const existing = Array.from(pillsContainer.querySelectorAll('.selected-model-pill'))
                                  .map(p => p.dataset.value);

            if (existing.includes(modelValue)) return;

            addPill(modelValue);
            updateHiddenInput();
        });

        pillsContainer.addEventListener('click', function(e) {
            if (e.target.classList.contains('remove-model')) {
                e.target.closest('.selected-model-pill').remove();
                updateHiddenInput();
            }
        });

        function addPill(value) {
            const pill = document.createElement('div');
            pill.className = 'selected-model-pill bg-gray-100 dark:bg-gray-700 px-3 py-1 rounded-full flex items-center gap-2 border dark:border-gray-600';
            pill.dataset.value = value;
            pill.innerHTML = `
                <span class="text-sm">${value}</span>
                <button type="button" class="remove-model text-red-500 font-bold">&times;</button>
            `;
            pillsContainer.appendChild(pill);
        }

        function updateHiddenInput() {
            const values = Array.from(pillsContainer.querySelectorAll('.selected-model-pill'))
                                .map(p => p.dataset.value);
            hiddenInput.value = JSON.stringify(values);
        }

        // Initial update
        updateHiddenInput();
    });
});
