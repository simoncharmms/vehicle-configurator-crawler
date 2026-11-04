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
let optionSummary = null;   // from index.json → option_summary(_by_market)
let chartInstance = null;
let barChartInstance = null;
let rangeChartInstance = null;
let comparisonRows = [];
let indexData = null;       // raw index.json
let availableMarkets = [];  // [{ code, name, currency }]
let currentMarket = 'DE';
let currentCurrency = 'EUR';
let comparableModelMap = new Map();
let activeDashboardTab = 'overview';

const BRAND_COLORS = {
  'mercedes-benz': '#00adef',
  'mercedesbenz':  '#00adef',
  'mercedes_benz': '#00adef',
  'audi':          '#bb0a30',
  'volkswagen':    '#001e50',
  'vw':            '#001e50',
  'range-rover':   '#3f5d42',
  'rangerover':    '#3f5d42',
  'porsche':       '#c0a062',
  'bmw':           '#1c69d4',
  'tesla':         '#cc0000',
};

const FOCUS_BRANDS = ['Mercedes-Benz', 'Audi', 'Volkswagen', 'Range Rover'];
const BRAND_ORDER = [
  ...FOCUS_BRANDS,
  'Porsche',
  'BMW',
  'Lexus',
  'Polestar',
  'BYD',
  'XPeng',
  'Zeekr',
];

// ---------- Initialization ----------

document.addEventListener('DOMContentLoaded', async () => {
  setupDashboardTabs();

  const ok = await loadIndex();
  if (ok) {
    populateMarketSelect();
    window.OptionIntelligence?.initialize();
    await loadMarketData(currentMarket);
  }
  populateFilters();
  setupCheckboxDropdown('brand-filter', 'All Brands');
  setupCheckboxDropdown('category-filter', 'All Categories');
  setupMarketSelect();
  updateStats();
  renderOptionTable();
  renderChart();
  renderVehicles();

  if (activeDashboardTab === 'intelligence') {
    renderIntelligencePanels();
    window.OptionIntelligence?.filtersChanged();
  }

});

// ---------- Data Loading ----------

async function loadIndex() {
  try {
    const indexResp = await fetch(`${DATA_BASE}/index.json`);
    if (!indexResp.ok) {
      showNoData('No data available yet. Run the crawler first.');
      return false;
    }
    indexData = await indexResp.json();
    availableMarkets = (indexData.available_markets || []).length
      ? indexData.available_markets
      : [{ code: 'DE', name: 'Germany', currency: 'EUR' }];
    if (!availableMarkets.some(m => m.code === currentMarket)) {
      currentMarket = availableMarkets[0].code;
    }
    return true;
  } catch (e) {
    console.error('Failed to load index:', e);
    showNoData('Failed to load data. Make sure the crawler has run at least once.');
    return false;
  }
}

/** Brand → snapshot list for one market (falls back to the legacy flat tree). */
function brandsForMarket(market) {
  const scoped = indexData?.markets?.[market]?.brands;
  if (scoped && Object.keys(scoped).length) return scoped;
  return market === 'DE' ? (indexData?.brands || {}) : {};
}

async function loadMarketData(market) {
  try {
    currentMarket = market;
    currentCurrency =
      availableMarkets.find(m => m.code === market)?.currency || 'EUR';
    allData = {};
    comparisonRows = [];

    optionSummary =
      indexData?.option_summary_by_market?.[market]
      || (market === 'DE' ? indexData?.option_summary : null)
      || null;
    if (optionSummary?.options) {
      optionSummary = {
        ...optionSummary,
        options: optionSummary.options.filter(row => !isExcludedOption(row)),
      };
    }

    const comparableRows = indexData?.comparable_models_by_market?.[market]?.vehicles || [];
    comparableModelMap = new Map(
      comparableRows.map(row => [row.vehicle_key, row]),
    );

    window.OptionIntelligence?.marketChanged();

    for (const [brandKey, brandInfo] of Object.entries(brandsForMarket(market))) {
      allData[brandKey] = { name: brandInfo.name, snapshots: {} };

      for (const snap of brandInfo.snapshots || []) {
        try {
          const compressed = indexData?.snapshot_encoding === 'gzip';
          const resp = await fetch(`${DATA_BASE}/${snap.file}${compressed ? '.gz' : ''}`);
          if (resp.ok) {
            let data;
            if (compressed) {
              const bytes = new Uint8Array(await resp.arrayBuffer());
              const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
              data = JSON.parse(await new Response(stream).text());
            } else {
              data = await resp.json();
            }
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
    updateMarketSubtitle();
  } catch (e) {
    console.error('Failed to load data:', e);
    showNoData('Failed to load data. Make sure the crawler has run at least once.');
  }
}

// ---------- Country selector ----------

function populateMarketSelect() {
  const select = document.getElementById('market-filter');
  if (!select) return;
  select.innerHTML = availableMarkets
    .map(m => `<option value="${escapeHtml(m.code)}">${escapeHtml(m.name || m.code)} (${escapeHtml(m.currency || 'EUR')})</option>`)
    .join('');
  select.value = currentMarket;
}

function setupMarketSelect() {
  const select = document.getElementById('market-filter');
  if (!select) return;
  select.addEventListener('change', async event => {
    await loadMarketData(event.target.value);
    populateFilters();
    onFilterChange();
  });
}

function updateMarketSubtitle() {
  const subtitle = document.getElementById('market-subtitle');
  if (!subtitle) return;
  const market = availableMarkets.find(m => m.code === currentMarket);
  const name = market?.name || currentMarket;
  subtitle.textContent = `Cross-brand option pricing from vehicle configurators — ${name} (${currentCurrency})`;
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

  brands.sort((left, right) => compareBrandPriority(left.label, right.label));

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
  window.OptionIntelligence?.filtersChanged();
  updateStats();
  renderOptionTable();
  renderChart();
  renderVehicles();

  if (activeDashboardTab === 'intelligence') {
    renderIntelligencePanels();
  }
}

function setupDashboardTabs() {
  const buttons = [...document.querySelectorAll('.dashboard-tab')];
  if (!buttons.length) return;

  const setTab = tab => {
    activeDashboardTab = tab === 'intelligence' ? 'intelligence' : 'overview';
    document.body.dataset.dashboardTab = activeDashboardTab;

    for (const button of buttons) {
      const isActive = button.dataset.tab === activeDashboardTab;
      button.classList.toggle('active', isActive);
      button.setAttribute('aria-pressed', String(isActive));
    }

    if (activeDashboardTab === 'intelligence') {
      renderIntelligencePanels();
      window.OptionIntelligence?.filtersChanged();
    }
  };

  for (const button of buttons) {
    button.addEventListener('click', () => setTab(button.dataset.tab || 'overview'));
  }

  setTab(activeDashboardTab);
}

function renderIntelligencePanels() {
  renderBarChart();
  renderRangeChart();
  renderComparableModels();
}

// ---------- Stats ----------

function updateStats() {
  const vehicles = getFilteredVehicles();
  const brands = new Set(vehicles.map(v => v.brandKey));

  // Count unique options
  const optionNames = new Set();
  let listPriceSum = 0;
  let listPriceCount = 0;

  for (const v of vehicles) {
    if (v.base_price && v.base_price > 0) {
      listPriceSum += v.base_price;
      listPriceCount++;
    }
    for (const opt of (v.available_options || [])) {
      const key = opt.standardized_name || opt.brand_specific_name;
      if (key) optionNames.add(key);
    }
  }

  // Also count from summary
  if (optionSummary && optionSummary.options) {
    for (const row of optionSummary.options) {
      optionNames.add(row.standardized_name);
    }
  }

  const avgListPrice = listPriceCount
    ? listPriceSum / listPriceCount
    : 0;

  document.getElementById('total-brands').textContent = brands.size || Object.keys(allData).length;
  document.getElementById('total-options').textContent = optionNames.size || '-';
  document.getElementById('avg-option-price').textContent = avgListPrice
    ? formatMoney(avgListPrice)
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
  if (!optionSummary?.options) {
    comparisonRows = [];
    return;
  }

  comparisonRows = optionSummary.options
    .map(row => {
      const pricedBrands = Object.entries(row.brands || {})
        .filter(([, info]) => info && info.avg_price != null)
        .map(([name, info]) => [name, info]);
      if (pricedBrands.length < 2) return null;

      const brandMap = Object.fromEntries(pricedBrands);
      const focusBrandCount = pricedBrands.filter(([name]) => FOCUS_BRANDS.includes(name)).length;

      return {
        ...row,
        brands: brandMap,
        cross_brand_count: pricedBrands.length,
        focus_brand_count: focusBrandCount,
      };
    })
    .filter(Boolean)
    .sort((left, right) => {
      if (right.focus_brand_count !== left.focus_brand_count) {
        return right.focus_brand_count - left.focus_brand_count;
      }
      return (right.total_model_count || 0) - (left.total_model_count || 0);
    });
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
      const marketName =
        availableMarkets.find(m => m.code === currentMarket)?.name || currentMarket;
      sourceLabel.textContent = `Live-extracted pricing from vehicle configurators in ${marketName}`;
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
      const priceStr = `<span class="brand-price">${formatMoney(info.avg_price)}</span>`;
      return `<td class="col-brand brand-cell">
        <span class="brand-option-name">${escapeHtml(info.name)}</span>
        ${priceStr}
      </td>`;
    }).join('');

    const avgPrice = row.overall_avg_price != null
      ? formatMoney(row.overall_avg_price)
      : '—';
    const range = (row.overall_min_price != null && row.overall_max_price != null)
      ? `${formatMoney(row.overall_min_price)} – ${formatMoney(row.overall_max_price)}`
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
    const color = colorForBrand(brand);

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
                ? `${ctx.dataset.label}: ${formatMoney(val)}`
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
            callback: v => formatMoney(v),
          },
          grid: { color: '#2a2d3a' },
          title: { display: true, text: `Average Option Price (${currentCurrency})`, color: '#8b8fa3' },
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
  const brandScores = new Map();
  for (const row of rows) {
    for (const [brand, info] of Object.entries(row.brands || {})) {
      if (info?.avg_price == null) continue;
      if (selectedBrandNames.size && !selectedBrandNames.has(brand)) continue;
      const prev = brandScores.get(brand) || 0;
      brandScores.set(brand, prev + 1);
    }
  }

  const brands = [...brandScores.keys()]
    .sort((left, right) => {
      const priority = compareBrandPriority(left, right);
      if (priority !== 0) return priority;
      return (brandScores.get(right) || 0) - (brandScores.get(left) || 0);
    })
    .slice(0, 4);

  if (!rows.length || !brands.length) return;

  const datasets = brands.map(brand => {
    const color = colorForBrand(brand);
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
            label: ctx => `${ctx.dataset.label}: ${formatMoney(ctx.parsed.x)}`,
          },
        },
      },
      scales: {
        x: {
          ticks: { color: '#8b8fa3', callback: value => formatMoney(value) },
          grid: { color: '#2a2d3a' },
          title: { display: true, text: `Average Price (${currentCurrency})`, color: '#8b8fa3' },
        },
        y: {
          ticks: { color: '#8b8fa3', font: { size: 11 } },
          grid: { color: '#2a2d3a' },
        },
      },
    },
  });
}

function filteredComparisonRows() {
  const categoryFilter = getSelectedValues('category-filter');
  const selectedBrandNames = new Set(getSelectedValues('brand-filter').map(key => allData[key]?.name));
  return comparisonRows.filter(row =>
    (!categoryFilter.length || categoryFilter.includes(row.category_label)) &&
    (!selectedBrandNames.size || [...selectedBrandNames].some(name => row.brands[name]))
  );
}

function renderRangeChart() {
  const canvas = document.getElementById('option-price-range-chart');
  if (!canvas) return;
  if (rangeChartInstance) rangeChartInstance.destroy();

  const rows = filteredComparisonRows()
    .filter(row => row.overall_min_price != null && row.overall_max_price != null && row.overall_avg_price != null)
    .slice(0, 20);
  if (!rows.length) return;

  const labels = rows.map(row => row.display_name || row.standardized_name);
  const ranges = rows.map(row => [row.overall_min_price, row.overall_max_price]);
  const avgPoints = rows.map((row, idx) => ({ x: row.overall_avg_price, y: labels[idx] }));

  rangeChartInstance = new Chart(canvas.getContext('2d'), {
    data: {
      labels,
      datasets: [
        {
          type: 'bar',
          label: 'Price range',
          data: ranges,
          backgroundColor: 'rgba(79, 143, 247, 0.22)',
          borderColor: 'rgba(79, 143, 247, 0.9)',
          borderWidth: 1,
          borderRadius: 3,
        },
        {
          type: 'scatter',
          label: 'Average list price',
          data: avgPoints,
          pointRadius: 4,
          pointHoverRadius: 5,
          pointBackgroundColor: '#ffb74d',
          pointBorderColor: '#ffb74d',
          showLine: false,
        },
      ],
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
            label: ctx => {
              if (ctx.dataset.type === 'bar') {
                const values = ctx.raw || [];
                if (!Array.isArray(values) || values.length !== 2) return 'Price range: —';
                return `Price range: ${formatMoney(values[0])} - ${formatMoney(values[1])}`;
              }
              return `Average list price: ${formatMoney(ctx.raw?.x)}`;
            },
          },
        },
      },
      scales: {
        x: {
          ticks: { color: '#8b8fa3', callback: value => formatMoney(value) },
          grid: { color: '#2a2d3a' },
          title: { display: true, text: `List price (${currentCurrency})`, color: '#8b8fa3' },
        },
        y: {
          ticks: { color: '#8b8fa3', font: { size: 11 } },
          grid: { color: '#2a2d3a' },
        },
      },
    },
  });
}

function categoryLabelForOptionKey(optionKey) {
  return optionSummary?.options?.find(row => row.standardized_name === optionKey)?.category_label || '';
}

function optionLabel(optionKey) {
  return optionSummary?.options?.find(row => row.standardized_name === optionKey)?.display_name
    || optionKey.replaceAll('_', ' ');
}

function renderComparableModels() {
  const container = document.getElementById('comparable-models-container');
  if (!container) return;

  const selectedCategories = getSelectedValues('category-filter');
  const vehicles = getFilteredVehicles();
  const latest = {};
  for (const vehicle of vehicles) {
    const key = `${vehicle.brandKey}|${vehicle.model}|${vehicle.variant || ''}`;
    if (!latest[key] || vehicle.date > latest[key].date) {
      latest[key] = vehicle;
    }
  }

  const rows = Object.values(latest)
    .map(vehicle => ({ vehicle, comparable: comparableModelMap.get(vehicleComparableKey(vehicle)) }))
    .filter(row => row.comparable)
    .filter(row => {
      if (!selectedCategories.length) return true;
      return (row.comparable.shared_options || []).some(optionKey => {
        const category = categoryLabelForOptionKey(optionKey);
        return category && selectedCategories.includes(category);
      });
    })
    .sort((left, right) => {
      const priority = compareBrandPriority(
        left.vehicle.brandName || left.vehicle.brand,
        right.vehicle.brandName || right.vehicle.brand,
      );
      if (priority !== 0) return priority;
      return (right.comparable.score || 0) - (left.comparable.score || 0);
    })
    .slice(0, 24);

  if (!rows.length) {
    container.innerHTML = '<div class="no-data"><p>No comparable model pairs match the current filters.</p></div>';
    return;
  }

  container.innerHTML = `<div class="comparable-grid">${rows.map(row => {
    const vehicle = row.vehicle;
    const comparable = row.comparable;
    const shared = (comparable.shared_options || []).slice(0, 4).map(optionLabel).join(' · ');
    return `
      <article class="comparable-card">
        <header>
          <span class="brand">${escapeHtml(vehicle.brandName || vehicle.brand)}</span>
          <h3>${escapeHtml(vehicle.model)}${vehicle.variant ? ` <span>${escapeHtml(vehicle.variant)}</span>` : ''}</h3>
          <p class="price">${vehicle.base_price ? formatMoney(vehicle.base_price) : 'Price on request'}</p>
        </header>
        <p class="reference">Reference: ${escapeHtml(comparable.reference_brand)} ${escapeHtml(comparable.reference_model)}${comparable.reference_variant ? ` (${escapeHtml(comparable.reference_variant)})` : ''}</p>
        <p class="reference-price">Ref ${formatMoney(comparable.reference_price)} · Delta ${formatSignedMoney(comparable.price_delta)}</p>
        <p class="meta">Shared options: ${comparable.shared_option_count || 0} · Score ${(comparable.score || 0).toFixed(2)}</p>
        ${shared ? `<p class="shared">${escapeHtml(shared)}</p>` : ''}
      </article>
    `;
  }).join('')}</div>`;
}

function compareBrandPriority(left, right) {
  const leftIdx = BRAND_ORDER.indexOf(left);
  const rightIdx = BRAND_ORDER.indexOf(right);
  const leftRank = leftIdx === -1 ? Number.MAX_SAFE_INTEGER : leftIdx;
  const rightRank = rightIdx === -1 ? Number.MAX_SAFE_INTEGER : rightIdx;
  if (leftRank !== rightRank) return leftRank - rightRank;
  return left.localeCompare(right);
}

function colorForBrand(brand) {
  const normalized = (brand || '').toLowerCase().replace(/[^a-z0-9]/g, '');
  const colorKey = Object.keys(BRAND_COLORS).find(key =>
    normalized.includes(key.replace(/[^a-z0-9]/g, '')),
  );
  if (colorKey) return BRAND_COLORS[colorKey];

  const fallback = ['#6f8cc3', '#c48352', '#4d9a7a', '#9b6c9d', '#c96f7f', '#728f5c'];
  const hash = [...normalized].reduce((acc, char) => acc + char.charCodeAt(0), 0);
  return fallback[hash % fallback.length];
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

/** Format a price in the currency of the currently selected country. */
function formatMoney(value) {
  return new Intl.NumberFormat('de-DE', {
    style: 'currency',
    currency: currentCurrency || 'EUR',
    maximumFractionDigits: 0,
  }).format(value);
}

function formatSignedMoney(value) {
  if (value == null) return '—';
  if (value === 0) return `±${formatMoney(0)}`;
  const sign = value > 0 ? '+' : '-';
  return `${sign}${formatMoney(Math.abs(value))}`;
}

function vehicleComparableKey(vehicle) {
  return `${vehicle.brandName || vehicle.brand}|${vehicle.model}|${vehicle.variant || ''}`;
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

  const sorted = Object.values(latest).sort((left, right) => {
    const byBrand = compareBrandPriority(left.brandName || left.brand, right.brandName || right.brand);
    if (byBrand !== 0) return byBrand;
    return (right.base_price || 0) - (left.base_price || 0);
  });

  if (sorted.length === 0) {
    container.innerHTML = '<div class="no-data"><p>No vehicles found.</p><p>Run the crawler to populate data.</p></div>';
    return;
  }

  container.innerHTML = sorted.map(v => {
    const optCount = (v.available_options || []).length;
    const optBadge = optCount > 0
      ? `<span class="option-count">${optCount} options</span>`
      : '';
    const comparable = comparableModelMap.get(vehicleComparableKey(v));
    const comparableHtml = comparable
      ? `<div class="comparable-model">Comparable: ${escapeHtml(comparable.reference_brand)} ${escapeHtml(comparable.reference_model)}${comparable.reference_variant ? ` (${escapeHtml(comparable.reference_variant)})` : ''} · Ref ${formatMoney(comparable.reference_price)} · Δ ${formatSignedMoney(comparable.price_delta)}</div>`
      : '';

    return `
    <div class="vehicle-card">
      <span class="brand">${escapeHtml(v.brandName || v.brand)}</span>
      <div class="model">${escapeHtml(v.model)}</div>
      ${v.variant ? `<div class="variant">${escapeHtml(v.variant)}</div>` : ''}
      <div class="price">${v.base_price ? formatMoney(v.base_price) : 'Price on request'}</div>
      ${comparableHtml}
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
