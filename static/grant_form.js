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

    const s = new Date(startInput.value);
    const e = new Date(endInput.value);

    if (isNaN(s) || isNaN(e) || e < s) return [];

    const years = [];
    for (let y = s.getFullYear(); y <= e.getFullYear(); y++) {
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

      // Helper: copy first year's value to all other inputs in this row
      function syncRowHours() {
        if (!inputs.length) return;
        const firstVal = inputs[0].value;
        for (let i = 1; i < inputs.length; i++) {
          inputs[i].value = firstVal;
        }
      }

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

        personnel.push({
          name: nameInput.value.trim(),
          position: posSelect ? posSelect.value.trim() : '',
          same_each_year: sameChk ? !!sameChk.checked : false,
          hours
        });
      });

      const persField = document.getElementById('personnel_json');
      if (persField) persField.value = JSON.stringify(personnel);

      // DOMESTIC TRAVEL JSON
      const domesticTrips = [];
      document.querySelectorAll('#domestic-travel-list .travel-item').forEach(item => {
        const name  = item.querySelector('input[name="domestic_travel_name[]"]')?.value || '';
        const desc  = item.querySelector('textarea[name="domestic_travel_desc[]"]')?.value || '';
        const year  = item.querySelector('select[name="domestic_travel_year[]"]')?.value || '';
        const dep   = item.querySelector('input[name="domestic_travel_depart[]"]')?.value || '';
        const arr   = item.querySelector('input[name="domestic_travel_arrive[]"]')?.value || '';
        const flt   = item.querySelector('input[name="domestic_travel_flight[]"]')?.value || '';
        const taxi  = item.querySelector('input[name="domestic_travel_taxi[]"]')?.value || '';
        const food  = item.querySelector('input[name="domestic_travel_food[]"]')?.value || '';
        const days  = item.querySelector('input[name="domestic_travel_days[]"]')?.value || '';

        const allEmpty = !name && !desc && !year && !dep && !arr && !flt && !taxi && !food && !days;
        if (allEmpty) return;

        domesticTrips.push({
          name,
          description: desc,
          year,
          depart: dep,
          arrive: arr,
          flight: flt  ? parseFloat(flt)  : null,
          taxi_per_day: taxi ? parseFloat(taxi) : null,
          food_per_day: food ? parseFloat(food) : null,
          days: days ? parseFloat(days) : null
        });
      });

      const domField = document.getElementById('domestic_travel_json');
      if (domField) domField.value = JSON.stringify(domesticTrips);

      // INTERNATIONAL TRAVEL JSON
      const intlTrips = [];
      document.querySelectorAll('#international-travel-list .travel-item').forEach(item => {
        const name  = item.querySelector('input[name="international_travel_name[]"]')?.value || '';
        const desc  = item.querySelector('textarea[name="international_travel_desc[]"]')?.value || '';
        const year  = item.querySelector('select[name="international_travel_year[]"]')?.value || '';
        const dep   = item.querySelector('input[name="international_travel_depart[]"]')?.value || '';
        const arr   = item.querySelector('input[name="international_travel_arrive[]"]')?.value || '';
        const flt   = item.querySelector('input[name="international_travel_flight[]"]')?.value || '';
        const taxi  = item.querySelector('input[name="international_travel_taxi[]"]')?.value || '';
        const food  = item.querySelector('input[name="international_travel_food[]"]')?.value || '';
        const days  = item.querySelector('input[name="international_travel_days[]"]')?.value || '';

        const allEmpty = !name && !desc && !year && !dep && !arr && !flt && !taxi && !food && !days;
        if (allEmpty) return;

        intlTrips.push({
          name,
          description: desc,
          year,
          depart: dep,
          arrive: arr,
          flight: flt  ? parseFloat(flt)  : null,
          taxi_per_day: taxi ? parseFloat(taxi) : null,
          food_per_day: food ? parseFloat(food) : null,
          days: days ? parseFloat(days) : null
        });
      });

      const intlField = document.getElementById('international_travel_json');
      if (intlField) intlField.value = JSON.stringify(intlTrips);

      // MATERIALS JSON
      const materials = [];
      document.querySelectorAll('#materials-list .material-item').forEach(item => {
        const category = item.querySelector('select[name="material_category[]"]')?.value || '';
        const cost     = item.querySelector('input[name="material_cost[]"]')?.value || '';
        const desc     = item.querySelector('textarea[name="material_desc[]"]')?.value || '';
        const year     = item.querySelector('select[name="material_year[]"]')?.value || '';

        if (!category && !cost && !desc && !year) return;

        materials.push({
          category,
          cost: cost ? parseFloat(cost) : null,
          description: desc,
          year
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

        if (nameInput) nameInput.value = p.name || '';
        if (posSelect) posSelect.value = p.position || '';
        if (sameChk)   sameChk.checked = !!p.same_each_year;

        personnelTable.appendChild(row);
      });

      // Build hours inputs for each row (based on date range)
      renderHoursPerYear();

      // Now fill the hours values
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

        const nameInput   = block.querySelector('input[name="domestic_travel_name[]"]');
        const descArea    = block.querySelector('textarea[name="domestic_travel_desc[]"]');
        const yearSelect  = block.querySelector('select[name="domestic_travel_year[]"]');
        const departInput = block.querySelector('input[name="domestic_travel_depart[]"]');
        const arriveInput = block.querySelector('input[name="domestic_travel_arrive[]"]');
        const flightInput = block.querySelector('input[name="domestic_travel_flight[]"]');
        const taxiInput   = block.querySelector('input[name="domestic_travel_taxi[]"]');
        const foodInput   = block.querySelector('input[name="domestic_travel_food[]"]');
        const daysInput   = block.querySelector('input[name="domestic_travel_days[]"]');

        const yearVal   = t.year != null ? String(t.year) : '';
        const departVal = t.start_date || t.depart || '';
        const arriveVal = t.end_date   || t.arrive || '';
        const flightVal = t.flight_cost || t.flight || '';
        const taxiVal   = t.taxi_per_day || '';
        const foodVal   = t.food_lodge_per_day || t.food_per_day || '';
        const daysVal   = t.num_days || t.days || '';

        if (nameInput)   nameInput.value   = t.travel_name || t.name || '';
        if (descArea)    descArea.value    = t.description || '';
        if (departInput) departInput.value = departVal;
        if (arriveInput) arriveInput.value = arriveVal;
        if (flightInput && flightVal !== '') flightInput.value = flightVal;
        if (taxiInput   && taxiVal   !== '') taxiInput.value   = taxiVal;
        if (foodInput   && foodVal   !== '') foodInput.value   = foodVal;
        if (daysInput   && daysVal   !== '') daysInput.value   = daysVal;

        // append now; we will set year after renderHoursPerYear repopulates options
        domesticList.appendChild(block);

        if (yearSelect && yearVal) {
          yearSelect.value = yearVal;
        }
      });

      // repopulate year dropdown options
      renderHoursPerYear();

      // now set the year values again to be safe
      const items = domesticList.querySelectorAll('.travel-item');
      items.forEach((block, idx) => {
        const t = window.INIT_DOM_TRAVEL[idx];
        if (!t) return;
        const yearSelect = block.querySelector('select[name="domestic_travel_year[]"]');
        if (yearSelect && t.year != null) {
          yearSelect.value = String(t.year);
        }
      });
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

        const nameInput   = block.querySelector('input[name="international_travel_name[]"]');
        const descArea    = block.querySelector('textarea[name="international_travel_desc[]"]');
        const yearSelect  = block.querySelector('select[name="international_travel_year[]"]');
        const departInput = block.querySelector('input[name="international_travel_depart[]"]');
        const arriveInput = block.querySelector('input[name="international_travel_arrive[]"]');
        const flightInput = block.querySelector('input[name="international_travel_flight[]"]');
        const taxiInput   = block.querySelector('input[name="international_travel_taxi[]"]');
        const foodInput   = block.querySelector('input[name="international_travel_food[]"]');
        const daysInput   = block.querySelector('input[name="international_travel_days[]"]');

        const yearVal   = t.year != null ? String(t.year) : '';
        const departVal = t.start_date || t.depart || '';
        const arriveVal = t.end_date   || t.arrive || '';
        const flightVal = t.flight_cost || t.flight || '';
        const taxiVal   = t.taxi_per_day || '';
        const foodVal   = t.food_lodge_per_day || t.food_per_day || '';
        const daysVal   = t.num_days || t.days || '';

        if (nameInput)   nameInput.value   = t.travel_name || t.name || '';
        if (descArea)    descArea.value    = t.description || '';
        if (departInput) departInput.value = departVal;
        if (arriveInput) arriveInput.value = arriveVal;
        if (flightInput && flightVal !== '') flightInput.value = flightVal;
        if (taxiInput   && taxiVal   !== '') taxiInput.value   = taxiVal;
        if (foodInput   && foodVal   !== '') foodInput.value   = foodVal;
        if (daysInput   && daysVal   !== '') daysInput.value   = daysVal;

        internationalList.appendChild(block);

        if (yearSelect && yearVal) {
          yearSelect.value = yearVal;
        }
      });

      renderHoursPerYear();

      const items = internationalList.querySelectorAll('.travel-item');
      items.forEach((block, idx) => {
        const t = window.INIT_INTL_TRAVEL[idx];
        if (!t) return;
        const yearSelect = block.querySelector('select[name="international_travel_year[]"]');
        if (yearSelect && t.year != null) {
          yearSelect.value = String(t.year);
        }
      });
    }

    // ---------- 4) MATERIALS ----------
    if (Array.isArray(window.INIT_MATERIALS) &&
        window.INIT_MATERIALS.length &&
        materialsList &&
        materialTemplate) {

      materialsList.innerHTML = '';

      window.INIT_MATERIALS.forEach((m) => {
        const block = materialTemplate.cloneNode(true);
        clearMaterialBlock(block);

        const catSelect = block.querySelector('select[name="material_category[]"]');
        const costInput = block.querySelector('input[name="material_cost[]"]');
        const descArea  = block.querySelector('textarea[name="material_desc[]"]');
        const yearSelect= block.querySelector('select[name="material_year[]"]');

        const catVal  = m.material_type || m.category || '';
        const costVal = m.cost != null ? m.cost : '';
        const yearVal = m.year != null ? String(m.year) : '';

        if (catSelect) catSelect.value = catVal;
        if (costInput && costVal !== '') costInput.value = costVal;
        if (descArea)  descArea.value  = m.description || '';

        materialsList.appendChild(block);

        if (yearSelect && yearVal) {
          yearSelect.value = yearVal;
        }
      });

      renderHoursPerYear();

      const items = materialsList.querySelectorAll('.material-item');
      items.forEach((block, idx) => {
        const m = window.INIT_MATERIALS[idx];
        if (!m) return;
        const yearSelect = block.querySelector('select[name="material_year[]"]');
        if (yearSelect && m.year != null) {
          yearSelect.value = String(m.year);
        }
      });
    }
  }

  try {
    prefillFromExistingJson();
  } catch (err) {
    console.error('Error pre-filling form from existing JSON:', err);
  }
});
