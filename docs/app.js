/**
 * Vehicle Configurator Price Tracker — Dashboard
 *
 * Reads JSON data from ../data/prices/ (or from the same repo on GitHub Pages).
 * Uses Chart.js for price trend visualization.
 */

// Resolve data path: works both locally (../data/prices) and on GitHub Pages (data/prices)
const DATA_BASE = document.location.pathname.includes('/docs/') ? '../data/prices' : 'data/prices';
let allData = {};   // { brand: { date: CrawlResult } }
let chartInstance = null;

const BRAND_COLORS = {
  'mercedes-benz': '#00adef',
  'mercedesbenz':  '#00adef',
  'audi':          '#bb0a30',
  'porsche':       '#c0a062',
  'bmw':           '#1c69d4',
  'tesla':         '#cc0000',
};

// ---------- Initialization ----------

document.addEventListener('DOMContentLoaded', async () => {
  await loadData();
  populateFilters();
  updateStats();
  renderChart();
  renderVehicles();

  document.getElementById('brand-filter').addEventListener('change', onFilterChange);
  document.getElementById('model-filter').addEventListener('change', onFilterChange);
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
  const models = new Set();

  for (const [key, brand] of Object.entries(allData)) {
    const opt = document.createElement('option');
    opt.value = key;
    opt.textContent = brand.name;
    brandSel.appendChild(opt);

    for (const snapshots of Object.values(brand.snapshots)) {
      for (const snap of snapshots) {
        for (const v of (snap.vehicles || [])) {
          models.add(v.model);
        }
      }
    }
  }

  const modelSel = document.getElementById('model-filter');
  for (const m of [...models].sort()) {
    const opt = document.createElement('option');
    opt.value = m;
    opt.textContent = m;
    modelSel.appendChild(opt);
  }
}

function getFilteredVehicles() {
  const brandFilter = document.getElementById('brand-filter').value;
  const modelFilter = document.getElementById('model-filter').value;
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
          if (modelFilter !== 'all' && v.model !== modelFilter) continue;
          result.push({ ...v, date, brandKey: key, brandName: brand.name });
        }
      }
    }
  }

  return result;
}

function onFilterChange() {
  updateStats();
  renderChart();
  renderVehicles();
}

// ---------- Stats ----------

function updateStats() {
  const vehicles = getFilteredVehicles();
  const brands = new Set(vehicles.map(v => v.brandKey));
  const models = new Set(vehicles.map(v => v.model));
  const prices = vehicles.filter(v => v.base_price).map(v => v.base_price);
  const avgPrice = prices.length ? prices.reduce((a, b) => a + b, 0) / prices.length : 0;

  document.getElementById('total-brands').textContent = brands.size;
  document.getElementById('total-vehicles').textContent = models.size;
  document.getElementById('avg-price').textContent = avgPrice
    ? `€${Math.round(avgPrice).toLocaleString('de-DE')}`
    : '-';

  // Last updated
  const dates = vehicles.map(v => v.date).sort();
  document.getElementById('last-updated').textContent = dates.length
    ? dates[dates.length - 1]
    : '-';
}

// ---------- Chart ----------

function renderChart() {
  const vehicles = getFilteredVehicles();
  const ctx = document.getElementById('price-chart').getContext('2d');

  if (chartInstance) chartInstance.destroy();

  // Group by model + date for trend lines
  const modelDates = {};
  for (const v of vehicles) {
    if (!v.base_price) continue;
    const key = `${v.brandName} ${v.model}`;
    if (!modelDates[key]) modelDates[key] = { brand: v.brandKey, points: {} };
    modelDates[key].points[v.date] = v.base_price;
  }

  // Get all unique dates sorted
  const allDates = [...new Set(vehicles.map(v => v.date))].sort();

  if (allDates.length === 0) {
    showNoData('No price data to chart.');
    return;
  }

  const datasets = Object.entries(modelDates).map(([label, info]) => {
    const color = BRAND_COLORS[info.brand] || '#888';
    return {
      label,
      data: allDates.map(d => info.points[d] || null),
      borderColor: color,
      backgroundColor: color + '20',
      tension: 0.3,
      spanGaps: true,
      pointRadius: 3,
    };
  });

  // Limit to top 15 by latest price to avoid legend clutter
  datasets.sort((a, b) => {
    const lastA = a.data.filter(x => x !== null).pop() || 0;
    const lastB = b.data.filter(x => x !== null).pop() || 0;
    return lastB - lastA;
  });
  const limited = datasets.slice(0, 15);

  chartInstance = new Chart(ctx, {
    type: 'line',
    data: { labels: allDates, datasets: limited },
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
            label: (ctx) => `${ctx.dataset.label}: €${ctx.parsed.y?.toLocaleString('de-DE') || '-'}`,
          },
        },
      },
      scales: {
        x: {
          ticks: { color: '#8b8fa3' },
          grid: { color: '#2a2d3a' },
        },
        y: {
          ticks: {
            color: '#8b8fa3',
            callback: (v) => `€${(v / 1000).toFixed(0)}k`,
          },
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

  // Show latest snapshot per model
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

  container.innerHTML = sorted.map(v => `
    <div class="vehicle-card">
      <span class="brand">${escapeHtml(v.brandName || v.brand)}</span>
      <div class="model">${escapeHtml(v.model)}</div>
      ${v.variant ? `<div class="variant">${escapeHtml(v.variant)}</div>` : ''}
      <div class="price">${v.base_price ? `€${v.base_price.toLocaleString('de-DE')}` : 'Price on request'}</div>
      ${v.fuel_type ? `<div class="fuel">${escapeHtml(v.fuel_type)}</div>` : ''}
    </div>
  `).join('');
}

// ---------- Helpers ----------

function showNoData(msg) {
  document.getElementById('vehicles-container').innerHTML =
    `<div class="no-data"><p>${msg}</p></div>`;
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str || '';
  return div.innerHTML;
}
