/**
 * Vehicle Option Price Tracker — Dashboard
 *
 * Reads JSON data from ../data/prices/ (or GitHub Pages data/prices).
 * Displays cross-brand option pricing table + price chart.
 */

const DATA_BASE = document.location.pathname.includes('/docs/')
  ? '../data/prices'
  : 'data/prices';

let allData = {};           // { brandKey: { name, snapshots: { date: [CrawlResult] } } }
let optionSummary = null;   // from index.json → option_summary
let chartInstance = null;
let barChartInstance = null;
let comparisonRows = [];

const BRAND_COLORS = {
  'mercedes-benz': '#00adef',
  'mercedesbenz':  '#00adef',
  'mercedes_benz': '#00adef',
  'audi':          '#bb0a30',
  'porsche':       '#c0a062',
  'bmw':           '#1c69d4',
  'tesla':         '#cc0000',
};

const BRAND_ORDER = ['Mercedes-Benz', 'Audi', 'Porsche', 'BMW'];

// ---------- Initialization ----------

document.addEventListener('DOMContentLoaded', async () => {
  await loadData();
  populateFilters();
  setupCheckboxDropdown('brand-filter', 'All Brands');
  setupCheckboxDropdown('category-filter', 'All Categories');
  updateStats();
  renderOptionTable();
  renderChart();
  renderBarChart();
  renderVehicles();

});

// ---------- Data Loading ----------

async function loadData() {
  try {
    const indexResp = await fetch(`${DATA_BASE}/index.json`);
    if (!indexResp.ok) {
      showNoData('No data available yet. Run the crawler first.');
      return;
    }
    const index = await indexResp.json();

    optionSummary = index.option_summary || null;
    if (optionSummary?.options) {
      optionSummary.options = optionSummary.options.filter(row => !isExcludedOption(row));
    }

    for (const [brandKey, brandInfo] of Object.entries(index.brands || {})) {
      allData[brandKey] = { name: brandInfo.name, snapshots: {} };

      for (const snap of brandInfo.snapshots || []) {
        try {
          const resp = await fetch(`${DATA_BASE}/${snap.file}`);
          if (resp.ok) {
            let data = await resp.json();
            if (!Array.isArray(data)) data = [data];
            allData[brandKey].snapshots[snap.date] = data.map(snapshot => ({
              ...snapshot,
              vehicles: (snapshot.vehicles || []).map(vehicle => ({
                ...vehicle,
                available_options: (vehicle.available_options || [])
                  .filter(option => !isExcludedOption(option)),
              })),
            }));
          }
        } catch (e) {
          console.warn(`Failed to load ${snap.file}:`, e);
        }
      }
    }
    buildComparisonRows();
  } catch (e) {
    console.error('Failed to load data:', e);
    showNoData('Failed to load data. Make sure the crawler has run at least once.');
  }
}

// ---------- Filters ----------

function populateFilters() {
  const brands = [];
  const categories = new Set();

  // Deduplicate brands by display name — only show brands with real vehicle data
  const seenBrandNames = new Set();
  for (const [key, brand] of Object.entries(allData)) {
    // Skip brands with no vehicles in any snapshot
    const hasVehicles = Object.values(brand.snapshots || {}).some(
      snaps => snaps.some(s => (s.vehicles || []).length > 0)
    );
    if (!hasVehicles) continue;
    // Skip duplicate display names
    if (seenBrandNames.has(brand.name)) continue;
    seenBrandNames.add(brand.name);

    brands.push({ value: key, label: brand.name });
  }

  // Populate categories from option summary
  if (optionSummary && optionSummary.options) {
    for (const row of optionSummary.options) {
      if (row.category_label) categories.add(row.category_label);
    }
  }

  populateCheckboxOptions('brand-filter', brands);
  populateCheckboxOptions(
    'category-filter',
    [...categories].sort().map(category => ({ value: category, label: category })),
  );
}

function getFilteredVehicles() {
  const brandFilter = getSelectedValues('brand-filter');

  const result = [];

  for (const [key, brand] of Object.entries(allData)) {
    if (brandFilter.length && !brandFilter.includes(key)) continue;

    for (const [date, snapshots] of Object.entries(brand.snapshots)) {
      for (const snap of snapshots) {
        for (const v of (snap.vehicles || [])) {
          result.push({ ...v, date, brandKey: key, brandName: brand.name });
        }
      }
    }
  }

  return result;
}

function onFilterChange() {
  updateStats();
  renderOptionTable();
  renderChart();
  renderBarChart();
  renderVehicles();
}

// ---------- Stats ----------

function updateStats() {
  const vehicles = getFilteredVehicles();
  const brands = new Set(vehicles.map(v => v.brandKey));

  // Count unique options
  const optionNames = new Set();
  let optionPriceSum = 0;
  let optionPriceCount = 0;

  for (const v of vehicles) {
    for (const opt of (v.available_options || [])) {
      const key = opt.standardized_name || opt.brand_specific_name;
      if (key) optionNames.add(key);
      if (opt.price && opt.price > 0) {
        optionPriceSum += opt.price;
        optionPriceCount++;
      }
    }
  }

  // Also count from summary
  if (optionSummary && optionSummary.options) {
    for (const row of optionSummary.options) {
      optionNames.add(row.standardized_name);
    }
  }

  const avgOptPrice = optionPriceCount
    ? optionPriceSum / optionPriceCount
    : (optionSummary && optionSummary.options
      ? avgFromSummary(optionSummary.options)
      : 0);

  document.getElementById('total-brands').textContent = brands.size || Object.keys(allData).length;
  document.getElementById('total-options').textContent = optionNames.size || '-';
  document.getElementById('avg-option-price').textContent = avgOptPrice
    ? `€${Math.round(avgOptPrice).toLocaleString('de-DE')}`
    : '-';

  const dates = vehicles.map(v => v.date).sort();
  document.getElementById('last-updated').textContent = dates.length
    ? dates[dates.length - 1]
    : (optionSummary ? optionSummary.last_updated?.split('T')[0] || '-' : '-');
}

function avgFromSummary(options) {
  const prices = options.filter(o => o.overall_avg_price).map(o => o.overall_avg_price);
  return prices.length ? prices.reduce((a, b) => a + b, 0) / prices.length : 0;
}

function isExcludedOption(option) {
  const name = [
    option?.standardized_name,
    option?.display_name,
    option?.brand_specific_name,
    option?.name,
  ].filter(Boolean).join(' ').toLowerCase();
  return name.includes('gesamtbetrag') || name.includes('total amount');
}

function buildComparisonRows() {
  const brand = allData['mercedes-benz'];
  if (!brand || !optionSummary?.options) return;

  const source = Object.entries(brand.snapshots || {})
    .sort(([dateA], [dateB]) => dateB.localeCompare(dateA))
    .map(([, snapshots]) => snapshots)
    .flat()
    .find(snapshot => (snapshot.vehicles || []).some(vehicle =>
      (vehicle.available_options || []).some(option => option.price > 0)
    ));
  if (!source) return;

  const mercedesOptions = new Map();
  for (const vehicle of source.vehicles || []) {
    for (const option of vehicle.available_options || []) {
      if (!option.price || option.price <= 0 || !option.standardized_name) continue;
      const entry = mercedesOptions.get(option.standardized_name) || {
        names: [],
        models: new Set(),
        prices: [],
      };
      if (option.brand_specific_name && !entry.names.includes(option.brand_specific_name)) {
        entry.names.push(option.brand_specific_name);
      }
      entry.models.add(vehicle.model);
      entry.prices.push(option.price);
      mercedesOptions.set(option.standardized_name, entry);
    }
  }

  comparisonRows = optionSummary.options.map(row => {
    const porsche = row.brands?.Porsche;
    const mercedes = mercedesOptions.get(row.standardized_name);
    if (!porsche?.avg_price || !mercedes) return null;

    const mercedesInfo = {
      name: mercedes.names[0] || row.standardized_name,
      avg_price: mercedes.prices.reduce((sum, price) => sum + price, 0) / mercedes.prices.length,
      min_price: Math.min(...mercedes.prices),
      max_price: Math.max(...mercedes.prices),
      model_count: mercedes.models.size,
    };
    const prices = [mercedesInfo.avg_price, porsche.avg_price];
    return {
      ...row,
      brands: { 'Mercedes-Benz': mercedesInfo, Porsche: porsche },
      cross_brand_count: 2,
      total_model_count: mercedesInfo.model_count + porsche.model_count,
      overall_avg_price: prices.reduce((sum, price) => sum + price, 0) / prices.length,
      overall_min_price: Math.min(...prices),
      overall_max_price: Math.max(...prices),
    };
  }).filter(Boolean);
}

// ---------- Option Comparison Table ----------

function renderOptionTable() {
  const container = document.getElementById('option-table-container');
  const tbody = document.getElementById('option-table-body');
  const thead = document.querySelector('#option-table thead tr');
  const brandFilter = getSelectedValues('brand-filter');
  const catFilter = getSelectedValues('category-filter');

  if (comparisonRows.length === 0) {
    tbody.innerHTML = '<tr><td colspan="99" class="no-data-cell">No option data available yet. Run the crawler to extract option pricing.</td></tr>';
    return;
  }

  // Show data source indicator
  const sourceLabel = document.getElementById('option-source-label');
  if (sourceLabel) {
    if (false) { // Reference data removed — live data only
    } else {
      sourceLabel.textContent = 'Live-extracted pricing from German vehicle configurators';
    }
  }

  // Determine which brands appear in the data
  const brandSet = new Set();
  for (const row of comparisonRows) {
    for (const b of Object.keys(row.brands || {})) {
      brandSet.add(b);
    }
  }
  const selectedBrandNames = new Set(brandFilter.map(key => allData[key]?.name));
  const brands = [...BRAND_ORDER.filter(b => brandSet.has(b)), ...[...brandSet].filter(b => !BRAND_ORDER.includes(b))]
    .filter((brand, index, list) => list.indexOf(brand) === index)
    .filter(brand => !selectedBrandNames.size || selectedBrandNames.has(brand));

  // Build header
  thead.innerHTML = `
    <th class="col-option">Option</th>
    <th class="col-category">Category</th>
    ${brands.map(b => `<th class="col-brand">${escapeHtml(b)}</th>`).join('')}
    <th class="col-price">Avg Price</th>
    <th class="col-range">Range</th>
    <th class="col-count">Models</th>
  `;

  // Filter options
  let rows = comparisonRows;
  if (catFilter.length) {
    rows = rows.filter(r => catFilter.includes(r.category_label));
  }
  if (selectedBrandNames.size) {
    rows = rows.filter(r => r.brands && [...selectedBrandNames].some(name => r.brands[name]));
  }

  if (rows.length === 0) {
    tbody.innerHTML = '<tr><td colspan="99" class="no-data-cell">No options match the current filters.</td></tr>';
    return;
  }

  tbody.innerHTML = rows.map(row => {
    const brandCells = brands.map(b => {
      const info = (row.brands || {})[b];
      if (!info || info.avg_price == null) return '<td class="col-brand brand-cell">—</td>';
      const priceStr = `<span class="brand-price">€${Math.round(info.avg_price).toLocaleString('de-DE')}</span>`;
      return `<td class="col-brand brand-cell">
        <span class="brand-option-name">${escapeHtml(info.name)}</span>
        ${priceStr}
      </td>`;
    }).join('');

    const avgPrice = row.overall_avg_price != null
      ? `€${Math.round(row.overall_avg_price).toLocaleString('de-DE')}`
      : '—';
    const range = (row.overall_min_price != null && row.overall_max_price != null)
      ? `€${Math.round(row.overall_min_price).toLocaleString('de-DE')} – €${Math.round(row.overall_max_price).toLocaleString('de-DE')}`
      : '—';

    return `<tr>
      <td class="col-option">
        <span class="option-name">${escapeHtml(row.display_name || row.standardized_name)}</span>
        <span class="option-key">${escapeHtml(row.standardized_name)}</span>
      </td>
      <td class="col-category"><span class="category-badge">${escapeHtml(row.category_label || row.category)}</span></td>
      ${brandCells}
      <td class="col-price avg-price">${avgPrice}</td>
      <td class="col-range">${range}</td>
      <td class="col-count">${row.total_model_count || '—'}</td>
    </tr>`;
  }).join('');
}

// ---------- Chart ----------

function renderChart() {
  const canvas = document.getElementById('price-chart');
  const ctx = canvas.getContext('2d');
  if (chartInstance) chartInstance.destroy();

  const categoryFilter = getSelectedValues('category-filter');
  const series = {};
  for (const vehicle of getFilteredVehicles()) {
    for (const option of vehicle.available_options || []) {
      if (!option.price || option.price <= 0) continue;
      if (categoryFilter.length && !categoryFilter.includes(getOptionCategory(option))) continue;
      const key = `${vehicle.brandName}|${vehicle.date}`;
      if (!series[key]) series[key] = { brand: vehicle.brandName, date: vehicle.date, sum: 0, count: 0 };
      series[key].sum += option.price;
      series[key].count += 1;
    }
  }

  const points = Object.values(series);
  const dates = [...new Set(points.map(point => point.date))].sort();
  const brands = [...new Set(points.map(point => point.brand))];
  if (!dates.length || !brands.length) return;
  const displayDates = dates.map(formatDate);

  const datasets = brands.map(brand => {
    const brandKey = Object.keys(BRAND_COLORS).find(k =>
      brand.toLowerCase().replace(/[- ]/g, '').includes(k.replace(/[_-]/g, ''))
    );
    const color = BRAND_COLORS[brandKey] || '#888';

    return {
      label: brand,
      data: dates.map(date => {
        const point = series[`${brand}|${date}`];
        return point ? point.sum / point.count : null;
      }),
      fill: false,
      borderColor: color,
      backgroundColor: color,
      borderWidth: 2,
      tension: 0.2,
      pointRadius: 4,
    };
  });

  chartInstance = new Chart(ctx, {
    type: 'line',
    data: { labels: displayDates, datasets },
    options: {
      responsive: true,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: {
          position: 'bottom',
          labels: { color: '#8b8fa3', font: { size: 11 }, boxWidth: 12 },
        },
        tooltip: {
          callbacks: {
            label: ctx => {
              const val = ctx.parsed.y;
              return val != null
                ? `${ctx.dataset.label}: ${formatEuro(val)}`
                : `${ctx.dataset.label}: —`;
            },
          },
        },
      },
      scales: {
        x: {
          ticks: { color: '#8b8fa3', maxRotation: 0, autoSkip: true },
          grid: { color: '#2a2d3a' },
          title: { display: true, text: 'Date', color: '#8b8fa3' },
        },
        y: {
          ticks: {
            color: '#8b8fa3',
            callback: v => formatEuro(v),
          },
          grid: { color: '#2a2d3a' },
          title: { display: true, text: 'Average Option Price (EUR)', color: '#8b8fa3' },
        },
      },
    },
  });
}

function renderBarChart() {
  const canvas = document.getElementById('option-price-bar-chart');
  if (!canvas) return;
  if (barChartInstance) barChartInstance.destroy();

  const categoryFilter = getSelectedValues('category-filter');
  const selectedBrandNames = new Set(getSelectedValues('brand-filter').map(key => allData[key]?.name));
  const rows = comparisonRows.filter(row =>
    (!categoryFilter.length || categoryFilter.includes(row.category_label)) &&
    (!selectedBrandNames.size || [...selectedBrandNames].some(name => row.brands[name]))
  );
  const brands = ['Mercedes-Benz', 'Porsche'].filter(brand =>
    !selectedBrandNames.size || selectedBrandNames.has(brand)
  );
  if (!rows.length || !brands.length) return;

  const datasets = brands.map(brand => {
    const color = brand === 'Mercedes-Benz' ? BRAND_COLORS['mercedes-benz'] : BRAND_COLORS.porsche;
    return {
      label: brand,
      data: rows.map(row => row.brands[brand]?.avg_price ?? null),
      backgroundColor: `${color}cc`,
      borderColor: color,
      borderWidth: 1,
    };
  });

  barChartInstance = new Chart(canvas.getContext('2d'), {
    type: 'bar',
    data: {
      labels: rows.map(row => row.display_name || row.standardized_name),
      datasets,
    },
    options: {
      responsive: true,
      indexAxis: 'y',
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: {
          position: 'bottom',
          labels: { color: '#8b8fa3', font: { size: 11 }, boxWidth: 12 },
        },
        tooltip: {
          callbacks: {
            label: ctx => `${ctx.dataset.label}: ${formatEuro(ctx.parsed.x)}`,
          },
        },
      },
      scales: {
        x: {
          ticks: { color: '#8b8fa3', callback: value => formatEuro(value) },
          grid: { color: '#2a2d3a' },
          title: { display: true, text: 'Average Price (EUR)', color: '#8b8fa3' },
        },
        y: {
          ticks: { color: '#8b8fa3', font: { size: 11 } },
          grid: { color: '#2a2d3a' },
        },
      },
    },
  });
}

function getSelectedValues(id) {
  const container = document.getElementById(id);
  const allOption = container.querySelector('input[value="all"]');
  if (!allOption || allOption.checked) return [];
  return [...container.querySelectorAll('input[type="checkbox"]:checked')]
    .map(option => option.value)
    .filter(value => value !== 'all');
}

function populateCheckboxOptions(id, options) {
  const container = document.querySelector(`#${id} .dropdown-options`);
  container.innerHTML = [
    { value: 'all', label: id === 'brand-filter' ? 'All Brands' : 'All Categories' },
    ...options,
  ].map(option => `
    <label>
      <input type="checkbox" value="${escapeHtml(option.value)}" ${option.value === 'all' ? 'checked' : ''}>
      <span>${escapeHtml(option.label)}</span>
    </label>
  `).join('');
}

function setupCheckboxDropdown(id, allLabel) {
  const container = document.getElementById(id);
  const toggle = container.querySelector('.dropdown-toggle');
  container.addEventListener('click', event => {
    if (event.target !== toggle) return;
    const isOpen = container.classList.toggle('open');
    toggle.setAttribute('aria-expanded', String(isOpen));
  });
  container.addEventListener('change', event => {
    if (!event.target.matches('input[type="checkbox"]')) return;
    const checkboxes = [...container.querySelectorAll('input[type="checkbox"]')];
    const allOption = checkboxes.find(option => option.value === 'all');
    if (event.target === allOption && allOption.checked) {
      checkboxes.filter(option => option !== allOption).forEach(option => { option.checked = false; });
    } else if (event.target !== allOption && event.target.checked) {
      allOption.checked = false;
    } else if (!checkboxes.some(option => option !== allOption && option.checked)) {
      allOption.checked = true;
    }
    updateDropdownLabel(container, allLabel);
    onFilterChange();
  });
}

function updateDropdownLabel(container, allLabel) {
  const selected = getSelectedValues(container.id);
  container.querySelector('.dropdown-toggle').textContent = selected.length
    ? `${selected.length} selected`
    : allLabel;
}

function getOptionCategory(option) {
  const row = optionSummary?.options?.find(item =>
    item.standardized_name === option.standardized_name
  );
  return row?.category_label || option.category || 'Other';
}

function formatDate(date) {
  return new Date(`${date}T00:00:00`).toLocaleDateString('de-DE', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  });
}

function formatEuro(value) {
  return new Intl.NumberFormat('de-DE', {
    style: 'currency',
    currency: 'EUR',
    maximumFractionDigits: 0,
  }).format(value);
}

// ---------- Vehicle Cards ----------

function renderVehicles() {
  const container = document.getElementById('vehicles-container');
  const vehicles = getFilteredVehicles();

  const latest = {};
  for (const v of vehicles) {
    const key = `${v.brandKey}|${v.model}`;
    if (!latest[key] || v.date > latest[key].date) {
      latest[key] = v;
    }
  }

  const sorted = Object.values(latest).sort((a, b) => (b.base_price || 0) - (a.base_price || 0));

  if (sorted.length === 0) {
    container.innerHTML = '<div class="no-data"><p>No vehicles found.</p><p>Run the crawler to populate data.</p></div>';
    return;
  }

  container.innerHTML = sorted.map(v => {
    const optCount = (v.available_options || []).length;
    const optBadge = optCount > 0
      ? `<span class="option-count">${optCount} options</span>`
      : '';

    return `
    <div class="vehicle-card">
      <span class="brand">${escapeHtml(v.brandName || v.brand)}</span>
      <div class="model">${escapeHtml(v.model)}</div>
      ${v.variant ? `<div class="variant">${escapeHtml(v.variant)}</div>` : ''}
      <div class="price">${v.base_price ? `€${v.base_price.toLocaleString('de-DE')}` : 'Price on request'}</div>
      ${v.fuel_type ? `<div class="fuel">${escapeHtml(v.fuel_type)}</div>` : ''}
      ${optBadge}
    </div>`;
  }).join('');
}

// ---------- Helpers ----------

function showNoData(msg) {
  document.getElementById('vehicles-container').innerHTML =
    `<div class="no-data"><p>${msg}</p></div>`;
  document.getElementById('option-table-body').innerHTML =
    `<tr><td colspan="99" class="no-data-cell">${msg}</td></tr>`;
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str || '';
  return div.innerHTML;
}
