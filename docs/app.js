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
  updateStats();
  renderOptionTable();
  renderChart();
  renderVehicles();

  document.getElementById('brand-filter').addEventListener('change', onFilterChange);
  document.getElementById('category-filter').addEventListener('change', onFilterChange);
  document.getElementById('date-range').addEventListener('change', onFilterChange);
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

    for (const [brandKey, brandInfo] of Object.entries(index.brands || {})) {
      allData[brandKey] = { name: brandInfo.name, snapshots: {} };

      for (const snap of brandInfo.snapshots || []) {
        try {
          const resp = await fetch(`${DATA_BASE}/${snap.file}`);
          if (resp.ok) {
            let data = await resp.json();
            if (!Array.isArray(data)) data = [data];
            allData[brandKey].snapshots[snap.date] = data;
          }
        } catch (e) {
          console.warn(`Failed to load ${snap.file}:`, e);
        }
      }
    }
  } catch (e) {
    console.error('Failed to load data:', e);
    showNoData('Failed to load data. Make sure the crawler has run at least once.');
  }
}

// ---------- Filters ----------

function populateFilters() {
  const brandSel = document.getElementById('brand-filter');
  const categories = new Set();

  for (const [key, brand] of Object.entries(allData)) {
    const opt = document.createElement('option');
    opt.value = key;
    opt.textContent = brand.name;
    brandSel.appendChild(opt);
  }

  // Populate categories from option summary
  if (optionSummary && optionSummary.options) {
    for (const row of optionSummary.options) {
      if (row.category_label) categories.add(row.category_label);
    }
  }

  const catSel = document.getElementById('category-filter');
  for (const c of [...categories].sort()) {
    const opt = document.createElement('option');
    opt.value = c;
    opt.textContent = c;
    catSel.appendChild(opt);
  }
}

function getFilteredVehicles() {
  const brandFilter = document.getElementById('brand-filter').value;
  const dateRange = document.getElementById('date-range').value;

  const cutoff = dateRange === 'all' ? null : new Date();
  if (cutoff) cutoff.setDate(cutoff.getDate() - parseInt(dateRange));

  const result = [];

  for (const [key, brand] of Object.entries(allData)) {
    if (brandFilter !== 'all' && key !== brandFilter) continue;

    for (const [date, snapshots] of Object.entries(brand.snapshots)) {
      if (cutoff && new Date(date) < cutoff) continue;

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

// ---------- Option Comparison Table ----------

function renderOptionTable() {
  const container = document.getElementById('option-table-container');
  const tbody = document.getElementById('option-table-body');
  const thead = document.querySelector('#option-table thead tr');
  const brandFilter = document.getElementById('brand-filter').value;
  const catFilter = document.getElementById('category-filter').value;

  if (!optionSummary || !optionSummary.options || optionSummary.options.length === 0) {
    tbody.innerHTML = '<tr><td colspan="99" class="no-data-cell">No option data available yet. Run the crawler to extract option pricing.</td></tr>';
    return;
  }

  // Show data source indicator
  const sourceLabel = document.getElementById('option-source-label');
  if (sourceLabel) {
    if (optionSummary.source === 'reference') {
      sourceLabel.innerHTML = 'Reference prices from German configurators <span class="source-badge">Reference Data</span>';
    } else {
      sourceLabel.textContent = 'Live-extracted pricing from German vehicle configurators';
    }
  }

  // Determine which brands appear in the data
  const brandSet = new Set();
  for (const row of optionSummary.options) {
    for (const b of Object.keys(row.brands || {})) {
      brandSet.add(b);
    }
  }
  const brands = BRAND_ORDER.filter(b => brandSet.has(b));
  // Add any brands not in the predefined order
  for (const b of brandSet) {
    if (!brands.includes(b)) brands.push(b);
  }

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
  let rows = optionSummary.options;
  if (catFilter !== 'all') {
    rows = rows.filter(r => r.category_label === catFilter);
  }
  if (brandFilter !== 'all') {
    const brandName = allData[brandFilter]?.name;
    if (brandName) {
      rows = rows.filter(r => r.brands && r.brands[brandName]);
    }
  }

  if (rows.length === 0) {
    tbody.innerHTML = '<tr><td colspan="99" class="no-data-cell">No options match the current filters.</td></tr>';
    return;
  }

  tbody.innerHTML = rows.map(row => {
    const brandCells = brands.map(b => {
      const info = (row.brands || {})[b];
      if (!info) return '<td class="col-brand brand-cell">—</td>';
      const priceStr = info.avg_price != null
        ? `<span class="brand-price">€${Math.round(info.avg_price).toLocaleString('de-DE')}</span>`
        : '';
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
  const ctx = document.getElementById('price-chart').getContext('2d');
  if (chartInstance) chartInstance.destroy();

  if (!optionSummary || !optionSummary.options || optionSummary.options.length === 0) {
    return;
  }

  const catFilter = document.getElementById('category-filter').value;
  const brandFilter = document.getElementById('brand-filter').value;

  let rows = optionSummary.options.filter(r => r.overall_avg_price != null);
  if (catFilter !== 'all') {
    rows = rows.filter(r => r.category_label === catFilter);
  }

  // Take top 15 by model count
  rows = rows.slice(0, 15);

  if (rows.length === 0) return;

  // Determine brands to show
  const brandSet = new Set();
  for (const row of rows) {
    for (const b of Object.keys(row.brands || {})) {
      brandSet.add(b);
    }
  }
  const brands = BRAND_ORDER.filter(b => brandSet.has(b));
  for (const b of brandSet) {
    if (!brands.includes(b)) brands.push(b);
  }

  // Filter brands
  const filteredBrands = brandFilter !== 'all'
    ? brands.filter(b => {
        const brandName = allData[brandFilter]?.name;
        return b === brandName;
      })
    : brands;

  const labels = rows.map(r => r.display_name || r.standardized_name);

  const datasets = filteredBrands.map(brand => {
    const brandKey = Object.keys(BRAND_COLORS).find(k =>
      brand.toLowerCase().replace(/[- ]/g, '').includes(k.replace(/[_-]/g, ''))
    );
    const color = BRAND_COLORS[brandKey] || '#888';

    return {
      label: brand,
      data: rows.map(r => {
        const info = (r.brands || {})[brand];
        return info?.avg_price ?? null;
      }),
      backgroundColor: color + 'cc',
      borderColor: color,
      borderWidth: 1,
    };
  });

  chartInstance = new Chart(ctx, {
    type: 'bar',
    data: { labels, datasets },
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
            label: ctx => {
              const val = ctx.parsed.x;
              return val != null
                ? `${ctx.dataset.label}: €${Math.round(val).toLocaleString('de-DE')}`
                : `${ctx.dataset.label}: —`;
            },
          },
        },
      },
      scales: {
        x: {
          ticks: {
            color: '#8b8fa3',
            callback: v => `€${(v / 1000).toFixed(1)}k`,
          },
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
