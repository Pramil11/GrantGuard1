document.addEventListener('DOMContentLoaded', function () {
  // Only run on the grant form page
  const personnelTable = document.getElementById('personnel-rows');
  if (!personnelTable) return;  // do nothing on other pages

  // Be robust: try by id first, then by name
  const startInput =
    document.getElementById('start_date') ||
    document.querySelector('input[name="start_date"]');
  const endInput =
    document.getElementById('end_date') ||
    document.querySelector('input[name="end_date"]');

  // ---------- DATE → YEARS HELPER ----------
  function getYearsInRange() {
    if (!startInput || !endInput) return [];
    if (!startInput.value || !endInput.value) return [];

    // Parse date strings directly to avoid timezone issues
    // Date format is YYYY-MM-DD
    const startParts = startInput.value.split('-');
    const endParts = endInput.value.split('-');
    
    if (startParts.length !== 3 || endParts.length !== 3) return [];
    
    const startYear = parseInt(startParts[0], 10);
    const startMonth = parseInt(startParts[1], 10);
    const startDay = parseInt(startParts[2], 10);
    
    const endYear = parseInt(endParts[0], 10);
    const endMonth = parseInt(endParts[1], 10);
    const endDay = parseInt(endParts[2], 10);
    
    // Validate dates
    if (isNaN(startYear) || isNaN(endYear)) return [];
    
    // Check if end date is before start date
    if (endYear < startYear || 
        (endYear === startYear && endMonth < startMonth) ||
        (endYear === startYear && endMonth === startMonth && endDay < startDay)) {
      return [];
    }
    
    const years = [];
    
    // Check if end date is January 1st - if so, exclude that year
    // because the research period doesn't actually include any days in that year
    const excludeEndYear = (endMonth === 1 && endDay === 1);
    const lastYear = excludeEndYear ? endYear - 1 : endYear;
    
    for (let y = startYear; y <= lastYear; y++) {
      years.push(y);
    }
    return years;
  }

  // ---------- PERSONNEL HOURS UI ----------
  function renderHoursPerYear() {
    const years = getYearsInRange();
    const rows = document.querySelectorAll('#personnel-rows tr');

    rows.forEach((row, rowIndex) => {
      const container = row.querySelector('.year-hours');
      const checkbox  = row.querySelector('input[name="personnel_same_each_year[]"]');
      if (!container) return;

      // Preserve existing hour values before clearing
      const existingInputs = container.querySelectorAll('input[type="number"]');
      const savedValues = [];
      existingInputs.forEach((inp, idx) => {
        savedValues[idx] = inp.value;
      });

      container.innerHTML = '';
      const inputs = [];

      // If no valid dates yet, just show a single Hours box
      if (!years.length) {
        const inp = document.createElement('input');
        inp.type = 'number';
        inp.min = '0';
        inp.placeholder = 'Hours';
        inp.name = `personnel_hours_${rowIndex}[]`;
        inp.style.width = '100%';
        container.appendChild(inp);
        inputs.push(inp);
      } else {
        years.forEach(year => {
          const wrapper = document.createElement('div');
          wrapper.style.display = 'flex';
          wrapper.style.gap = '6px';
          wrapper.style.marginBottom = '4px';

          const label = document.createElement('span');
          label.textContent = year;
          label.style.minWidth = '50px';

          const inp = document.createElement('input');
          inp.type = 'number';
          inp.min = '0';
          inp.placeholder = 'Hours';
          inp.name = `personnel_hours_${rowIndex}[]`;
          inp.style.flex = '1';

          wrapper.appendChild(label);
          wrapper.appendChild(inp);
          container.appendChild(wrapper);

          inputs.push(inp);
        });
      }

      // Restore saved values if they exist
      if (savedValues.length > 0) {
        inputs.forEach((inp, idx) => {
          if (idx < savedValues.length && savedValues[idx]) {
            inp.value = savedValues[idx];
          }
        });
      }

      // Helper: copy first year's value to all other inputs in this row
      function syncRowHours() {
        if (!inputs.length) return;
        const firstVal = inputs[0].value;
        for (let i = 1; i < inputs.length; i++) {
          inputs[i].value = firstVal;
        }
        calculatePersonnelTotal(row);
      }

      // Calculate total for this personnel row (sum of all year hours * rate)
      function calculatePersonnelTotal(personRow) {
        const rateInput = personRow.querySelector('.person-rate');
        const totalInput = personRow.querySelector('.person-total');
        if (!rateInput || !totalInput) return;

        const rate = parseFloat(rateInput.value) || 0;
        const hourInputs = container.querySelectorAll('input[type="number"]');
        let totalHours = 0;

        hourInputs.forEach(inp => {
          const hours = parseFloat(inp.value) || 0;
          totalHours += hours;
        });

        const total = totalHours * rate;
        totalInput.value = total.toFixed(2);
      }

      // Add event listeners for rate and hours to calculate total
      const rateInput = row.querySelector('.person-rate');
      if (rateInput) {
        rateInput.addEventListener('input', () => calculatePersonnelTotal(row));
      }
      inputs.forEach(inp => {
        inp.addEventListener('input', () => calculatePersonnelTotal(row));
      });

      // When checkbox is toggled ON, copy first year's hours to all years once
      if (checkbox) {
        checkbox.onchange = () => {
          if (checkbox.checked) {
            syncRowHours();
          }
        };
      }

      // While checkbox is ON, changing first year's hours keeps others in sync
      if (inputs.length && checkbox) {
        const firstInput = inputs[0];
        firstInput.addEventListener('input', () => {
          if (checkbox.checked) {
            syncRowHours();
          }
        });
      }
    });

    // Travel year dropdowns (also used by materials section)
    const travelYearSelects = document.querySelectorAll('.travel-year');
    if (travelYearSelects.length) {
      const years = getYearsInRange();
      travelYearSelects.forEach(sel => {
        const current = sel.value;
        sel.innerHTML = '<option value="">Select Year</option>';
        years.forEach(y => {
          const opt = document.createElement('option');
          opt.value = y;
          opt.textContent = y;
          sel.appendChild(opt);
        });
        if (current) sel.value = current;
      });
    }
  }

  // Re-render hours when dates change
  if (startInput && endInput) {
    startInput.addEventListener('change', renderHoursPerYear);
    endInput.addEventListener('change', renderHoursPerYear);
  }
  // Initial render (for edit mode / prefilled dates)
  renderHoursPerYear();

  // ---------- PERSONNEL ADD / REMOVE ----------
  const addPersonBtn = document.getElementById('add-personnel');
  let personTemplate = null;

  if (personnelTable) {
    const firstRow = personnelTable.querySelector('.person-row') || personnelTable.querySelector('tr');
    if (firstRow) {
      personTemplate = firstRow.cloneNode(true);
    }
  }

  function clearPersonRow(row) {
    row.querySelectorAll('input, select').forEach(el => {
      if (el.type === 'checkbox') {
        el.checked = false;
      } else {
        el.value = '';
      }
    });
    const container = row.querySelector('.year-hours');
    if (container) container.innerHTML = '';
  }

    if (addPersonBtn && personTemplate) {
    addPersonBtn.addEventListener('click', () => {
      const clone = personTemplate.cloneNode(true);
      clearPersonRow(clone);
      clone.classList.add('person-row');
      personnelTable.appendChild(clone);
      renderHoursPerYear();
      
      // Add event listeners for the new row's rate and hours
      const rateInput = clone.querySelector('.person-rate');
      const hourInputs = clone.querySelectorAll('.year-hours input[type="number"]');
      if (rateInput) {
        rateInput.addEventListener('input', () => {
          const totalInput = clone.querySelector('.person-total');
          const rate = parseFloat(rateInput.value) || 0;
          let totalHours = 0;
          hourInputs.forEach(inp => {
            totalHours += parseFloat(inp.value) || 0;
          });
          if (totalInput) {
            totalInput.value = (totalHours * rate).toFixed(2);
          }
        });
      }
      hourInputs.forEach(inp => {
        inp.addEventListener('input', () => {
          const totalInput = clone.querySelector('.person-total');
          const rateInput = clone.querySelector('.person-rate');
          const rate = parseFloat(rateInput?.value) || 0;
          let totalHours = 0;
          clone.querySelectorAll('.year-hours input[type="number"]').forEach(hInp => {
            totalHours += parseFloat(hInp.value) || 0;
          });
          if (totalInput) {
            totalInput.value = (totalHours * rate).toFixed(2);
          }
        });
      });
    });

    personnelTable.addEventListener('click', (e) => {
      if (e.target.classList.contains('person-remove')) {
        const row = e.target.closest('tr');
        // keep at least one row
        if (row && personnelTable.rows.length > 1) {
          row.remove();
          renderHoursPerYear();
        }
      }
    });
  }

  // ---------- TRAVEL ADD/REMOVE ----------
  const domesticList   = document.getElementById('domestic-travel-list');
  const addDomesticBtn = document.getElementById('add-domestic-travel');

  const internationalList   = document.getElementById('international-travel-list');
  const addInternationalBtn = document.getElementById('add-international-travel');

  function initTravelList(list) {
    if (!list) return { template: null };

    const rows = Array.from(list.querySelectorAll('.travel-row'));
    if (!rows.length) return { template: null };

    const block = document.createElement('div');
    block.className = 'travel-item';

    rows.forEach(row => block.appendChild(row)); // moves rows into block

    list.innerHTML = '';
    list.appendChild(block);

    const template = block.cloneNode(true);
    return { template };
  }

  const { template: domesticTemplate }      = initTravelList(domesticList);
  const { template: internationalTemplate } = initTravelList(internationalList);

  function clearTravelBlock(block) {
    block.querySelectorAll('input, textarea, select').forEach(el => {
      if (el.type === 'checkbox' || el.type === 'radio') {
        el.checked = false;
      } else {
        el.value = '';
      }
    });
  }

  if (domesticList && addDomesticBtn && domesticTemplate) {
    addDomesticBtn.addEventListener('click', () => {
      const clone = domesticTemplate.cloneNode(true);
      clearTravelBlock(clone);
      domesticList.appendChild(clone);
      renderHoursPerYear();  // refresh year dropdowns
    });

    domesticList.addEventListener('click', (e) => {
      if (e.target.classList.contains('travel-remove')) {
        const item = e.target.closest('.travel-item');
        if (item) item.remove();
      }
    });
  }

  if (internationalList && addInternationalBtn && internationalTemplate) {
    addInternationalBtn.addEventListener('click', () => {
      const clone = internationalTemplate.cloneNode(true);
      clearTravelBlock(clone);
      internationalList.appendChild(clone);
      renderHoursPerYear();
    });

    internationalList.addEventListener('click', (e) => {
      if (e.target.classList.contains('travel-remove')) {
        const item = e.target.closest('.travel-item');
        if (item) item.remove();
      }
    });
  }

  // ---------- MATERIALS & SUPPLIES ADD/REMOVE ----------
  const materialsList  = document.getElementById('materials-list');
  const addMaterialBtn = document.getElementById('add-material');

  let materialTemplate = null;

  if (materialsList) {
    const firstItem = materialsList.querySelector('.material-item');
    if (firstItem) {
      materialTemplate = firstItem.cloneNode(true);
    }
  }

  // ---------- EQUIPMENT ADD/REMOVE ----------
  const equipmentList  = document.getElementById('equipment-list');
  const addEquipmentBtn = document.getElementById('add-equipment');

  let equipmentTemplate = null;

  if (equipmentList) {
    const firstItem = equipmentList.querySelector('.material-item');
    if (firstItem) {
      equipmentTemplate = firstItem.cloneNode(true);
    }
  }

  if (equipmentList && addEquipmentBtn && equipmentTemplate) {
    addEquipmentBtn.addEventListener('click', () => {
      const clone = equipmentTemplate.cloneNode(true);
      clearMaterialBlock(clone);
      equipmentList.appendChild(clone);
      renderHoursPerYear();
    });

    equipmentList.addEventListener('click', (e) => {
      if (e.target.classList.contains('material-remove')) {
        const item = e.target.closest('.material-item');
        if (item) item.remove();
      }
    });
  }

  // ---------- OTHER DIRECT COSTS ADD/REMOVE ----------
  const otherCostsList  = document.getElementById('other-costs-list');
  const addOtherCostBtn = document.getElementById('add-other-cost');

  let otherCostTemplate = null;

  if (otherCostsList) {
    const firstItem = otherCostsList.querySelector('.material-item');
    if (firstItem) {
      otherCostTemplate = firstItem.cloneNode(true);
    }
  }

  if (otherCostsList && addOtherCostBtn && otherCostTemplate) {
    addOtherCostBtn.addEventListener('click', () => {
      const clone = otherCostTemplate.cloneNode(true);
      clearMaterialBlock(clone);
      otherCostsList.appendChild(clone);
      renderHoursPerYear();
    });

    otherCostsList.addEventListener('click', (e) => {
      if (e.target.classList.contains('material-remove')) {
        const item = e.target.closest('.material-item');
        if (item) item.remove();
      }
    });
  }

  function clearMaterialBlock(block) {
    block.querySelectorAll('input, textarea, select').forEach(el => {
      if (el.type === 'checkbox' || el.type === 'radio') {
        el.checked = false;
      } else {
        el.value = '';
      }
    });
  }

  if (materialsList && addMaterialBtn && materialTemplate) {
    addMaterialBtn.addEventListener('click', () => {
      const clone = materialTemplate.cloneNode(true);
      clearMaterialBlock(clone);
      materialsList.appendChild(clone);
      renderHoursPerYear();   // to populate year dropdowns
    });

    materialsList.addEventListener('click', (e) => {
      if (e.target.classList.contains('material-remove')) {
        const item = e.target.closest('.material-item');
        if (item) item.remove();
      }
    });
  }

  // ---------- JSON PACKING BEFORE SUBMIT ----------
  const form = document.getElementById('awardForm');

  if (form) {
    form.addEventListener('submit', function () {
      const years = getYearsInRange();

      // PERSONNEL JSON
      const personnel = [];
      const personRows = document.querySelectorAll('#personnel-rows tr');
      personRows.forEach((row) => {
        const nameInput = row.querySelector('input[name="personnel_name[]"]');
        const posSelect = row.querySelector('select[name="personnel_position[]"]');
        if (!nameInput || !nameInput.value.trim()) return;

        const sameChk = row.querySelector('input[name="personnel_same_each_year[]"]');
        const container = row.querySelector('.year-hours');
        const hourInputs = container ? container.querySelectorAll('input[type="number"]') : [];

        const hours = [];
        if (years.length && years.length === hourInputs.length) {
          years.forEach((y, idx) => {
            const val = hourInputs[idx].value;
            if (val !== '') {
              hours.push({ year: y, hours: parseFloat(val) });
            }
          });
        } else {
          hourInputs.forEach((inp) => {
            if (inp.value !== '') {
              hours.push({ year: null, hours: parseFloat(inp.value) });
            }
          });
        }

        const rateInput = row.querySelector('.person-rate');
        const totalInput = row.querySelector('.person-total');
        const rate = rateInput ? (parseFloat(rateInput.value) || 0) : 0;
        const total = totalInput ? (parseFloat(totalInput.value) || 0) : 0;

        personnel.push({
          name: nameInput.value.trim(),
          position: posSelect ? posSelect.value.trim() : '',
          same_each_year: sameChk ? !!sameChk.checked : false,
          hours,
          rate_per_hour: rate,
          total: total
        });
      });

      const persField = document.getElementById('personnel_json');
      if (persField) persField.value = JSON.stringify(personnel);

      // DOMESTIC TRAVEL JSON
      const domesticTrips = [];
      document.querySelectorAll('#domestic-travel-list .travel-item').forEach(item => {
        const desc  = item.querySelector('textarea[name="domestic_travel_desc[]"]')?.value || '';
        const total = item.querySelector('input[name="domestic_travel_total[]"]')?.value || '';

        const allEmpty = !desc && !total;
        if (allEmpty) return;

        domesticTrips.push({
          description: desc,
          total_amount: total ? parseFloat(total) : null
        });
      });

      const domField = document.getElementById('domestic_travel_json');
      if (domField) domField.value = JSON.stringify(domesticTrips);

      // INTERNATIONAL TRAVEL JSON
      const intlTrips = [];
      document.querySelectorAll('#international-travel-list .travel-item').forEach(item => {
        const desc  = item.querySelector('textarea[name="international_travel_desc[]"]')?.value || '';
        const total = item.querySelector('input[name="international_travel_total[]"]')?.value || '';

        const allEmpty = !desc && !total;
        if (allEmpty) return;

        intlTrips.push({
          description: desc,
          total_amount: total ? parseFloat(total) : null
        });
      });

      const intlField = document.getElementById('international_travel_json');
      if (intlField) intlField.value = JSON.stringify(intlTrips);

      // MATERIALS JSON
      const materials = [];
      document.querySelectorAll('#materials-list .material-item').forEach(item => {
        const cost     = item.querySelector('input[name="material_cost[]"]')?.value || '';
        const desc     = item.querySelector('textarea[name="material_desc[]"]')?.value || '';

        if (!cost && !desc) return;

        materials.push({
          cost: cost ? parseFloat(cost) : null,
          description: desc
        });
      });

      // Add equipment to materials array with type='equipment'
      document.querySelectorAll('#equipment-list .material-item').forEach(item => {
        const cost     = item.querySelector('input[name="equipment_cost[]"]')?.value || '';
        const desc     = item.querySelector('textarea[name="equipment_desc[]"]')?.value || '';

        if (!cost && !desc) return;

        materials.push({
          type: 'equipment',
          cost: cost ? parseFloat(cost) : null,
          description: desc
        });
      });

      // Add other costs to materials array with type='other'
      document.querySelectorAll('#other-costs-list .material-item').forEach(item => {
        const cost     = item.querySelector('input[name="other_cost_amount[]"]')?.value || '';
        const desc     = item.querySelector('textarea[name="other_cost_desc[]"]')?.value || '';

        if (!cost && !desc) return;

        materials.push({
          type: 'other',
          cost: cost ? parseFloat(cost) : null,
          description: desc
        });
      });

      const matField = document.getElementById('materials_json');
      if (matField) matField.value = JSON.stringify(materials);
      // Let the form submit normally
    });
  }

  // ===============================
  // PREFILL FORM WHEN EDITING (INIT_ JSON FROM BACKEND)
  // ===============================
  function prefillFromExistingJson() {
    const years = getYearsInRange();

    // ---------- 1) PERSONNEL ----------
    if (Array.isArray(window.INIT_PERSONNEL) &&
        window.INIT_PERSONNEL.length &&
        personnelTable &&
        personTemplate) {

      // Remove default row(s)
      personnelTable.innerHTML = '';

      // Create one row per saved person
      window.INIT_PERSONNEL.forEach((p) => {
        const row = personTemplate.cloneNode(true);
        clearPersonRow(row);

        const nameInput = row.querySelector('input[name="personnel_name[]"]');
        const posSelect = row.querySelector('select[name="personnel_position[]"]');
        const sameChk   = row.querySelector('input[name="personnel_same_each_year[]"]');
        const rateInput = row.querySelector('.person-rate');
        const totalInput = row.querySelector('.person-total');

        if (nameInput) nameInput.value = p.name || '';
        if (posSelect) posSelect.value = p.position || '';
        if (sameChk)   sameChk.checked = !!p.same_each_year;
        if (rateInput && p.rate_per_hour != null) rateInput.value = p.rate_per_hour;
        if (totalInput && p.total != null) totalInput.value = p.total;

        personnelTable.appendChild(row);
      });

      // Build hours inputs for each row (based on date range)
      renderHoursPerYear();

      // Now fill the hours values and recalculate totals
      const rows = document.querySelectorAll('#personnel-rows tr');
      rows.forEach((row, idx) => {
        const p = window.INIT_PERSONNEL[idx] || {};
        const hoursArr = Array.isArray(p.hours) ? p.hours : [];
        const container = row.querySelector('.year-hours');
        if (!container) return;

        const hourInputs = container.querySelectorAll('input[type="number"]');

        if (!years.length) {
          // Only one box: use first hours element if present
          if (hourInputs[0] && hoursArr.length > 0 && hoursArr[0].hours != null) {
            hourInputs[0].value = hoursArr[0].hours;
          }
        } else {
          years.forEach((y, i) => {
            const inp = hourInputs[i];
            if (!inp) return;
            const match = hoursArr.find(h =>
              (h.year != null && String(h.year) === String(y)) || h.year == null
            );
            if (match && match.hours != null) {
              inp.value = match.hours;
            }
          });
        }

        // Trigger total calculation after hours are filled
        const rateInput = row.querySelector('.person-rate');
        if (rateInput) {
          const event = new Event('input', { bubbles: true });
          rateInput.dispatchEvent(event);
        }
      });
    }

    // ---------- 2) DOMESTIC TRAVEL ----------
    if (Array.isArray(window.INIT_DOM_TRAVEL) &&
        window.INIT_DOM_TRAVEL.length &&
        domesticList &&
        domesticTemplate) {

      domesticList.innerHTML = '';

      window.INIT_DOM_TRAVEL.forEach((t) => {
        const block = domesticTemplate.cloneNode(true);
        clearTravelBlock(block);

        const descArea    = block.querySelector('textarea[name="domestic_travel_desc[]"]');
        const totalInput  = block.querySelector('input[name="domestic_travel_total[]"]');

        // Calculate total from old structure if needed
        let totalVal = t.total_amount || 0;
        if (!totalVal && (t.flight || t.taxi_per_day || t.food_per_day)) {
          totalVal = (parseFloat(t.flight) || 0) + 
                     (parseFloat(t.taxi_per_day) || 0) + 
                     (parseFloat(t.food_per_day) || 0);
        }

        if (descArea)    descArea.value    = t.description || '';
        if (totalInput && totalVal) totalInput.value = totalVal;
        domesticList.appendChild(block);
      });

      // repopulate year dropdown options
      renderHoursPerYear();
    }

    // ---------- 3) INTERNATIONAL TRAVEL ----------
    if (Array.isArray(window.INIT_INTL_TRAVEL) &&
        window.INIT_INTL_TRAVEL.length &&
        internationalList &&
        internationalTemplate) {

      internationalList.innerHTML = '';

      window.INIT_INTL_TRAVEL.forEach((t) => {
        const block = internationalTemplate.cloneNode(true);
        clearTravelBlock(block);

        const descArea    = block.querySelector('textarea[name="international_travel_desc[]"]');
        const totalInput  = block.querySelector('input[name="international_travel_total[]"]');

        // Calculate total from old structure if needed
        let totalVal = t.total_amount || 0;
        if (!totalVal && (t.flight || t.taxi_per_day || t.food_per_day)) {
          totalVal = (parseFloat(t.flight) || 0) + 
                     (parseFloat(t.taxi_per_day) || 0) + 
                     (parseFloat(t.food_per_day) || 0);
        }

        if (descArea)    descArea.value    = t.description || '';
        if (totalInput && totalVal) totalInput.value = totalVal;
        internationalList.appendChild(block);
      });

      renderHoursPerYear();
    }

    // ---------- 4) MATERIALS, EQUIPMENT, OTHER COSTS ----------
    // Separate materials by type
    const materialsOnly = [];
    const equipmentOnly = [];
    const otherCostsOnly = [];

    if (Array.isArray(window.INIT_MATERIALS)) {
      window.INIT_MATERIALS.forEach(m => {
        if (m.type === 'equipment') {
          equipmentOnly.push(m);
        } else if (m.type === 'other') {
          otherCostsOnly.push(m);
        } else {
          materialsOnly.push(m);
        }
      });
    }

    // Materials
    if (materialsOnly.length && materialsList && materialTemplate) {
      materialsList.innerHTML = '';
      materialsOnly.forEach((m) => {
        const block = materialTemplate.cloneNode(true);
        clearMaterialBlock(block);

        const costInput = block.querySelector('input[name="material_cost[]"]');
        const descArea  = block.querySelector('textarea[name="material_desc[]"]');

        const costVal = m.cost != null ? m.cost : '';

        if (costInput && costVal !== '') costInput.value = costVal;
        if (descArea)  descArea.value  = m.description || '';

        materialsList.appendChild(block);
      });
      renderHoursPerYear();
    }

    // Equipment
    if (equipmentOnly.length && equipmentList && equipmentTemplate) {
      equipmentList.innerHTML = '';
      equipmentOnly.forEach((e) => {
        const block = equipmentTemplate.cloneNode(true);
        clearMaterialBlock(block);

        const costInput = block.querySelector('input[name="equipment_cost[]"]');
        const descArea  = block.querySelector('textarea[name="equipment_desc[]"]');

        const costVal = e.cost != null ? e.cost : '';

        if (costInput && costVal !== '') costInput.value = costVal;
        if (descArea)  descArea.value  = e.description || '';

        equipmentList.appendChild(block);
      });
      renderHoursPerYear();
    }

    // Other Costs
    if (otherCostsOnly.length && otherCostsList && otherCostTemplate) {
      otherCostsList.innerHTML = '';
      otherCostsOnly.forEach((o) => {
        const block = otherCostTemplate.cloneNode(true);
        clearMaterialBlock(block);

        const costInput = block.querySelector('input[name="other_cost_amount[]"]');
        const descArea  = block.querySelector('textarea[name="other_cost_desc[]"]');

        const costVal = o.cost != null ? o.cost : '';

        if (costInput && costVal !== '') costInput.value = costVal;
        if (descArea)  descArea.value  = o.description || '';

        otherCostsList.appendChild(block);
      });
      renderHoursPerYear();
    }
  }

  try {
    prefillFromExistingJson();
  } catch (err) {
    console.error('Error pre-filling form from existing JSON:', err);
  }
});
