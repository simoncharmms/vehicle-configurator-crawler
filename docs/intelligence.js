(() => {
  'use strict';

  const element = id => document.getElementById(`intelligence-${id}`);
  const PAGE_SIZE = 50;
  const cache = new Map();
  const detailCache = new Map();
  let detailRequest = 0;
  const state = {source: 'synthetic', horizon: 30, change: 0, page: 0, selected: null,
    payload: null, filtered: [], summaries: new Map(), request: 0, key: null, initialized: false};
  let manifestPromise = null;
  let priceChart = null;
  let rateChart = null;
  const normalize = value => String(value || '').toLowerCase().replace(/[^a-z0-9]/g, '');
  const readable = value => String(value || 'unavailable').replace(/_/g, ' ');
  const numeric = value => typeof value === 'number' && Number.isFinite(value);
  const count = value => numeric(value) ? value.toLocaleString() : 'Unavailable';
  const rate = value => numeric(value) ? `${(value * 100).toFixed(1)}%` : 'Unavailable';
  const points = value => numeric(value) ? `${value >= 0 ? '+' : ''}${(value * 100).toFixed(2)}` : 'Unavailable';
  const sourceLabel = () => state.source === 'synthetic' ? 'Synthetic demand' : 'Observed purchases';
  const label = series => [series.brand, series.vehicle_model, series.variant,
    series.option_name || series.standardized_name || series.option_code, series.option_code].filter(Boolean).join(' / ');
  const money = value => numeric(value) ? new Intl.NumberFormat(undefined, {
    style: 'currency', currency: state.payload?.currency || currentCurrency, maximumFractionDigits: 2,
  }).format(value) : 'Unavailable';
  const text = (id, value) => { element(id).textContent = value; };
  const node = (tag, value) => {
    const result = document.createElement(tag);
    if (value !== undefined) result.textContent = value;
    return result;
  };

  function status(message, kind = 'ready') {
    text('status', message);
    element('status').dataset.state = kind;
  }

  function artifactURL(path) {
    const base = new URL(`${DATA_BASE}/../intelligence/manifest.json`, document.baseURI);
    const result = new URL(path, base);
    if (result.origin !== base.origin || !['http:', 'https:'].includes(result.protocol)) {
      throw new Error('Artifact path must use the dashboard origin.');
    }
    return result.href;
  }

  async function fetchJSON(url) {
    const response = await fetch(url);
    if (response.status === 404) return null;
    if (!response.ok) throw new Error(`Request failed (HTTP ${response.status}).`);
    const bytes = new Uint8Array(await response.arrayBuffer());
    if (bytes[0] === 31 && bytes[1] === 139) {
      if (typeof DecompressionStream === 'undefined') throw new Error('This browser does not support compressed intelligence data. Use a current browser.');
      const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
      return JSON.parse(await new Response(stream).text());
    }
    return JSON.parse(new TextDecoder().decode(bytes));
  }

  function clearCharts() {
    priceChart?.destroy();
    rateChart?.destroy();
    priceChart = null;
    rateChart = null;
  }

  async function load(force = false) {
    const key = `${state.source}:${currentMarket}`;
    if (!force && state.key === key) return;
    state.key = key;
    const request = ++state.request;
    const market = currentMarket;
    const source = state.source;
    state.payload = null;
    state.filtered = [];
    state.summaries.clear();
    state.page = 0;
    element('content').hidden = true;
    element('cohorts').hidden = true;
    clearCharts();
    text('source-label', sourceLabel());
    text('source-caveat', source === 'synthetic'
      ? 'Synthetic exposures, selections and eligibility are simulated, not actual sales. Sensitivity may be configured.'
      : 'Observed purchases only. Missing observations are never replaced with synthetic demand; fallback sensitivity may still be a configured assumption.');
    status(`Loading ${sourceLabel().toLowerCase()} for ${market}...`, 'loading');
    try {
      if (force) manifestPromise = null;
      if (!manifestPromise) {
        manifestPromise = fetchJSON(artifactURL('manifest.json')).catch(error => {
          manifestPromise = null;
          throw error;
        });
      }
      const manifest = await manifestPromise;
      if (request !== state.request) return;
      if (!manifest) {
        manifestPromise = null;
        status('Intelligence artifacts have not been published yet. The price dashboard remains available.', 'empty');
        return;
      }
      if (manifest.schema_version !== 1) throw new Error('Unsupported intelligence manifest version.');
      const entry = manifest.sources?.[source];
      const endpoint = entry?.markets?.[market];
      if (!endpoint) {
        status(`${sourceLabel()} has no published endpoint for ${market}. No alternative demand source is substituted.`, 'empty');
        return;
      }
      const url = artifactURL(endpoint);
      if (force) cache.delete(url);
      if (!cache.has(url)) {
        cache.set(url, fetchJSON(url).catch(error => { cache.delete(url); throw error; }));
      }
      const payload = await cache.get(url);
      if (request !== state.request) return;
      if (!payload) {
        cache.delete(url);
        status(`The published ${sourceLabel().toLowerCase()} endpoint for ${market} is missing. Refresh after publication completes.`, 'empty');
        return;
      }
      if (payload.schema_version !== 1 || payload.market !== market || payload.demand_source !== source || !Array.isArray(payload.series)) {
        throw new Error('Artifact schema, market or demand source does not match the selection.');
      }
      const expectedCurrency = availableMarkets.find(item => item.code === market)?.currency;
      if (!payload.currency || (expectedCurrency && payload.currency !== expectedCurrency)) {
        throw new Error('Artifact currency does not match this market.');
      }
      state.payload = payload;
      for (const series of payload.series) {
        if (series.model && !series.model.transform) series.model.transform = payload.model_transform;
      }
      state.searchIndex = new Map(payload.series.map(series => [series.id, label(series).toLowerCase()]));
      element('content').hidden = false;
      const assumptions = element('assumptions');
      assumptions.replaceChildren(...(payload.assumptions || []).map(value => node('li', value)));
      const cohortPath = entry.observations_by_market?.[market] || entry.observations_path;
      if (cohortPath) {
        element('cohorts').href = artifactURL(cohortPath);
        element('cohorts').hidden = false;
        text('cohorts', `${sourceLabel()} aggregate cohort archive / ${market}`);
      }
      if (payload.status === 'no_observed_data') {
        status(`No observed purchase cohorts for ${market}. Catalogue series may be available; forecasts are unavailable.`, 'empty');
      } else if (payload.status === 'no_usable_prices' || !payload.series.length) {
        status(`No usable option price series for ${market} in this source.`, 'empty');
      } else {
        status(`${sourceLabel()} / ${market} / ${payload.currency} / as of ${payload.as_of || 'unknown'} / published ${payload.generated_at || 'unknown'}`);
      }
      renderFiltered();
    } catch (error) {
      if (request !== state.request) return;
      state.payload = null;
      element('content').hidden = true;
      status(`Intelligence unavailable: ${error.message} The price dashboard is independent.`, 'error');
    }
  }

  function renderFiltered() {
    if (!state.payload) return;
    const selectedBrands = getSelectedValues('brand-filter');
    const brandNames = new Set(selectedBrands.flatMap(key => [key, allData[key]?.name,
      indexData?.markets?.[currentMarket]?.brands?.[key]?.name, indexData?.brands?.[key]?.name]).map(normalize));
    const selectedCategories = new Set(getSelectedValues('category-filter').map(normalize));
    const categoryLabels = new Map();
    const summary = indexData?.option_summary_by_market?.[currentMarket]
      || (currentMarket === 'DE' ? indexData?.option_summary : null);
    for (const row of summary?.options || []) {
      if (row.category && row.category_label) categoryLabels.set(row.category, row.category_label);
    }
    const query = element('search').value.trim().toLowerCase();
    state.filtered = state.payload.series.filter(series =>
      (!selectedBrands.length || brandNames.has(normalize(series.brand))) &&
      (!selectedCategories.size || selectedCategories.has(normalize(series.category)) ||
        selectedCategories.has(normalize(categoryLabels.get(series.category)))) &&
      (!query || state.searchIndex.get(series.id).includes(query)));
    state.page = 0;
    let eligible = 0;
    let selected = 0;
    for (const series of state.filtered) {
      eligible += series.eligible_count || 0;
      selected += series.selected_count || 0;
    }
    text('totals', `${sourceLabel()} / ${state.filtered.length.toLocaleString()} of ${state.payload.series.length.toLocaleString()} series / ${count(selected)} selected / ${count(eligible)} ${state.source === 'synthetic' ? 'simulated option exposures (not actual sales)' : 'option exposures (not unique buyers)'} / full-history count-weighted rate ${rate(eligible ? selected / eligible : null)}.`);
    const candidates = state.filtered.slice(0, PAGE_SIZE);
    if (!state.filtered.some(series => series.id === state.selected)) state.selected = candidates[0]?.id || null;
    const chosen = state.filtered.find(series => series.id === state.selected);
    if (chosen && !candidates.includes(chosen)) candidates.push(chosen);
    element('series').replaceChildren(...candidates.map(series => {
      const option = node('option', label(series));
      option.value = series.id;
      return option;
    }));
    element('series').value = state.selected || '';
    element('series').disabled = !candidates.length;
    text('matches', `${state.filtered.length.toLocaleString()} matching series; selector shows up to ${PAGE_SIZE} matches plus the current selection. ${state.filtered.length > PAGE_SIZE ? 'Additional matches remain in the paginated ranking.' : ''}`);
    element('export').disabled = !state.filtered.length;
    renderTable();
    renderDetail();
  }

  function forecastSummary(series) {
    if (!state.summaries.has(series.id)) {
      const baseline = window.OptionIntelligenceModel.predictSeries(series, 0, 2);
      const scenario = state.change === 0 ? baseline : window.OptionIntelligenceModel.predictSeries(series, state.change, 2);
      state.summaries.set(series.id, {baseline30: baseline[1]?.rate, baseline90: baseline[1]?.rate,
        scenario30: scenario[1]?.rate, scenario90: scenario[1]?.rate});
    }
    return state.summaries.get(series.id);
  }

  function renderTable() {
    const sort = element('sort').value;
    const value = series => {
      if (sort === 'forecast') return forecastSummary(series)[`scenario${state.horizon}`];
      if (sort === 'delta') {
        const summary = forecastSummary(series);
        const scenario = summary[`scenario${state.horizon}`];
        return numeric(scenario) ? scenario - summary[`baseline${state.horizon}`] : null;
      }
      return series[sort];
    };
    const sorted = [...state.filtered].sort((first, second) => {
      if (sort === 'label') return label(first).localeCompare(label(second));
      const firstValue = value(first);
      const secondValue = value(second);
      return (numeric(secondValue) ? secondValue : -Infinity) - (numeric(firstValue) ? firstValue : -Infinity)
        || label(first).localeCompare(label(second));
    });
    const pageCount = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE));
    state.page = Math.min(state.page, pageCount - 1);
    const start = state.page * PAGE_SIZE;
    const rows = sorted.slice(start, start + PAGE_SIZE).map(series => {
      const summary = forecastSummary(series);
      const expected = summary[`scenario${state.horizon}`];
      const baseline = summary[`baseline${state.horizon}`];
      const row = node('tr');
      row.setAttribute('aria-current', String(series.id === state.selected));
      const name = node('td');
      const button = node('button', label(series));
      button.type = 'button';
      button.addEventListener('click', () => {
        state.selected = series.id;
        const select = element('series');
        if (![...select.options].some(option => option.value === series.id)) {
          const option = node('option', label(series));
          option.value = series.id;
          if (select.options.length > PAGE_SIZE) select.remove(select.options.length - 1);
          select.append(option);
        }
        select.value = series.id;
        renderTable();
        renderDetail();
      });
      name.append(button);
      row.append(name);
      const coverage = series.coverage || {};
      for (const content of [money(series.latest_price), `${count(series.selected_count)} / ${count(series.eligible_count)}`,
        rate(series.take_rate), rate(summary.scenario30), rate(summary.scenario90),
        points(numeric(expected) && numeric(baseline) ? expected - baseline : null),
        `${coverage.observed_days || 0} / ${coverage.calendar_span_days || 0} days; ${coverage.different_price_levels || 0} price levels`,
        readable(series.model.sensitivity_source)]) row.append(node('td', content));
      return row;
    });
    if (!rows.length) {
      const row = node('tr');
      const cell = node('td', 'No series match the selected market, source, brands, categories and search.');
      cell.colSpan = 9;
      row.append(cell);
      rows.push(row);
    }
    element('ranking').replaceChildren(...rows);
    text('table-label', `${sourceLabel()} / expected rates use the ${state.change}% price scenario; delta compares unchanged price at ${state.horizon} days. Historical ratios cover all imported history, not the latest day. Counts are ${state.source === 'synthetic' ? 'simulated exposures, not actual sales' : 'option exposures, not unique buyers'}.`);
    text('page', `${sorted.length ? start + 1 : 0}-${Math.min(start + PAGE_SIZE, sorted.length)} of ${sorted.length.toLocaleString()} / page ${state.page + 1} of ${pageCount}`);
    element('prev').disabled = state.page === 0;
    element('next').disabled = state.page + 1 >= pageCount;
  }

  async function renderDetail() {
    const request = ++detailRequest;
    const sourceRequest = state.request;
    clearCharts();
    let series = state.filtered.find(item => item.id === state.selected);
    element('detail').hidden = !series;
    if (!series) return;
    if (series.detail_path) {
      text('series-title', `${sourceLabel()} / ${label(series)}`);
      text('scenario-status', 'Loading selected option history and validation...');
      const url = artifactURL(series.detail_path);
      try {
        if (!detailCache.has(url)) {
          detailCache.set(url, fetchJSON(url).catch(error => { detailCache.delete(url); throw error; }));
        }
        const shard = await detailCache.get(url);
        if (request !== detailRequest || sourceRequest !== state.request) return;
        if (!shard || shard.market !== state.payload.market || shard.demand_source !== state.source) throw new Error('Option detail source or market mismatch.');
        const full = shard.series.find(item => item.id === series.id);
        if (!full) throw new Error('Selected option detail was not published.');
        series = full;
      } catch (error) {
        if (request === detailRequest && sourceRequest === state.request) {
          text('scenario-status', `Option detail unavailable: ${error.message}`);
          element('model-status').replaceChildren();
          element('warnings').replaceChildren();
          element('metrics').replaceChildren();
        }
        return;
      }
    }
    const model = series.model;
    const baseline = window.OptionIntelligenceModel.predictSeries(series, 0, state.horizon);
    const scenario = state.change === 0 ? baseline : window.OptionIntelligenceModel.predictSeries(series, state.change, state.horizon);
    text('series-title', `${sourceLabel()} / ${label(series)}`);
    const proposed = numeric(model.last_price) ? model.last_price * (1 + state.change / 100) : null;
    const range = model.scenario_price_range;
    const extrapolation = range && numeric(proposed) && (proposed < range[0] || proposed > range[1]);
    text('scenario-status', baseline.length
      ? `Unchanged reference ${money(model.last_price)}; scenario ${money(proposed)} (${state.change}%). Day ${state.horizon}: unchanged ${rate(baseline.at(-1)?.rate)}, scenario ${rate(scenario.at(-1)?.rate)}; ${points(scenario.at(-1).rate - baseline.at(-1).rate)} pp.${extrapolation ? ' Outside recorded price range: extrapolation, not a reliable elasticity estimate.' : ''}`
      : 'Forecast unavailable: insufficient positive purchase exposure, history or model convergence. No scenario has been fabricated.');
    const facts = [
      ['Model / availability', `${readable(model.kind)} / ${model.forecast_available ? 'forecast available' : 'forecast unavailable'}`],
      ['Sensitivity source', readable(model.sensitivity_source)],
      ['Training history', `${model.training_start || 'Unavailable'} to ${model.training_end || 'Unavailable'}; ${model.training_days || 0} positive-exposure days`],
      ['Training exposures', count(model.eligible_count)],
      ['Forecast origin / reference price', `${model.last_date || 'Unavailable'} / ${money(model.last_price)}`],
      ['Catalogue latest / as of', `${money(series.latest_price)} / ${series.coverage?.last_date || 'Unavailable'}`],
      ['Demand price sources', (series.price_sources || []).join(', ') || 'No purchase prices'],
      ['90-day validation', readable(series.metrics?.['90']?.status)],
      ['Series identity', series.id],
    ];
    element('model-status').replaceChildren(...facts.map(([name, value]) => {
      const group = node('div');
      group.append(node('dt', name), node('dd', value));
      return group;
    }));
    element('warnings').replaceChildren(...(series.warnings || []).map(warning => node('li', readable(warning))));
    renderMetrics(series);
    renderCharts(series, baseline, scenario);
  }

  function renderMetrics(series) {
    const rows = [];
    const dateNotes = [];
    const decimal = value => numeric(value) ? value.toFixed(4) : 'Unavailable';
    for (const horizon of [1, 30, 90]) {
      const metric = series.metrics?.[String(horizon)];
      const dates = metric?.evaluated_dates || [];
      dateNotes.push(`${horizon}d origins: ${(metric?.origins || []).join(', ') || 'none'}; evaluated: ${dates.length ? `${dates[0]} to ${dates.at(-1)} (${dates.length} dates)` : 'none'}`);
      for (const [key, name] of [['model', 'Model'], ['latest_day_smoothed_baseline', 'Latest-day smoothed baseline'], ['pooled_smoothed_baseline', 'Pooled per-series baseline']]) {
        const score = metric?.[key];
        const row = node('tr');
        for (const content of [`${horizon}d / ${readable(metric?.status)}`, name, decimal(score?.brier), decimal(score?.log_loss),
          `${rate(score?.observed_rate)} / ${rate(score?.predicted_rate)}`, points(score?.calibration_error), count(score?.eligible_count)]) row.append(node('td', content));
        rows.push(row);
      }
    }
    text('metrics-title', `${sourceLabel()} / rolling-origin validation`);
    element('metrics').replaceChildren(...rows);
    text('evaluation-dates', dateNotes.join('. '));
  }

  function renderCharts(series, baseline, scenario) {
    if (typeof Chart === 'undefined') {
      text('scenario-status', `${element('scenario-status').textContent} Charts unavailable: Chart.js did not load.`);
      return;
    }
    const history = series.history || [];
    const dates = [...new Set([...history.map(point => point.date), ...scenario.map(point => point.date)])].sort();
    const historical = new Map(history.map(point => [point.date, point]));
    const unchanged = new Map(baseline.map(point => [point.date, point]));
    const changed = new Map(scenario.map(point => [point.date, point]));
    const css = getComputedStyle(document.documentElement);
    const accent = css.getPropertyValue('--accent').trim();
    const success = css.getPropertyValue('--success').trim();
    const warning = css.getPropertyValue('--warning').trim();
    const dataFor = (mapping, key, multiplier = 1) => dates.map(day => {
      const value = mapping.get(day)?.[key];
      return numeric(value) ? value * multiplier : null;
    });
    const line = (name, data, color, future = false) => ({label: name, data, borderColor: color,
      backgroundColor: color, borderWidth: 2, pointRadius: future ? 0 : 2, borderDash: future ? [6, 4] : [], spanGaps: false});
    const options = isRate => ({responsive: true, maintainAspectRatio: false, animation: false,
      interaction: {mode: 'index', intersect: false},
      plugins: {legend: {labels: {color: css.getPropertyValue('--text').trim(),
        filter: item => Boolean(item.text)}}, tooltip: {filter: item => Boolean(item.dataset.label),
        callbacks: {label: item => `${item.dataset.label}: ${isRate ? rate(item.parsed.y / 100) : money(item.parsed.y)}`}}},
      scales: {x: {ticks: {color: css.getPropertyValue('--text-dim').trim(), maxTicksLimit: 6, maxRotation: 0}, grid: {display: false}},
        y: {min: isRate ? 0 : undefined, max: isRate ? 100 : undefined,
          ticks: {color: css.getPropertyValue('--text-dim').trim(), callback: value => isRate ? `${value}%` : money(value)},
          grid: {color: css.getPropertyValue('--border').trim()}}}});
    const futurePrice = (mapping, price) => dates.map(day => mapping.has(day) ? price : null);
    text('price-caption', `${sourceLabel()} / ${state.payload.currency} / catalogue context and demand prices`);
    text('rate-caption', `${sourceLabel()} / take rate / ${state.horizon}-day conditional projection`);
    priceChart = new Chart(element('price-chart'), {type: 'line', data: {labels: dates, datasets: [
      line('Catalogue-context price (artifact)', dataFor(historical, 'price'), accent),
      line('Demand cohort price', dataFor(historical, 'demand_price'), success),
      line('Unchanged reference price', futurePrice(unchanged, series.model.last_price), accent, true),
      line('Scenario price', futurePrice(changed, series.model.last_price * (1 + state.change / 100)), warning, true),
    ]}, options: options(false)});
    const interval = (mapping, color, name) => [
      {label: '', data: dataFor(mapping, 'lower', 100), borderWidth: 0, pointRadius: 0, backgroundColor: 'transparent'},
      {label: `${name} approximate conditional interval`, data: dataFor(mapping, 'upper', 100),
        borderWidth: 0, pointRadius: 0, fill: '-1', backgroundColor: color},
    ];
    rateChart = new Chart(element('rate-chart'), {type: 'line', data: {labels: dates, datasets: [
      ...interval(unchanged, 'rgba(79,143,247,0.12)', 'Unchanged'),
      ...interval(changed, 'rgba(255,152,0,0.12)', 'Scenario'),
      line(`${sourceLabel()} historical rate`, dataFor(historical, 'take_rate', 100), success),
      line('Unchanged expected rate', dataFor(unchanged, 'rate', 100), accent, true),
      line('Scenario expected rate', dataFor(changed, 'rate', 100), warning, true),
    ]}, options: options(true)});
  }

  function scenarioInput(value, fromSlider = false) {
    const input = element('change');
    const change = Number(value);
    if (value.trim() === '' || !Number.isFinite(change) || change < -100 || change > 200) {
      input.setCustomValidity('Enter a finite price change from -100% to 200%.');
      input.setAttribute('aria-invalid', 'true');
      text('scenario-status', 'Invalid price change. Enter a number from -100% to 200%; displayed results retain the last valid scenario.');
      return;
    }
    input.setCustomValidity('');
    input.removeAttribute('aria-invalid');
    if (fromSlider) input.value = String(change);
    element('change-slider').value = String(change);
    state.change = change;
    state.summaries.clear();
    renderTable();
    renderDetail();
  }

  function downloadCSV() {
    const columns = ['demand_source', 'price_sources', 'market', 'currency', 'series_id', 'brand', 'model', 'variant',
      'option_code', 'option_name', 'standardized_name', 'category', 'date', 'horizon', 'path', 'price', 'price_source',
      'pct_change', 'eligible_count', 'selected_count', 'rate', 'lower', 'upper', 'uncertainty_source', 'sensitivity_source'];
    const escape = value => {
      let content = value == null ? '' : String(value);
      if (typeof value === 'string' && /^[=+@\-\t\r]/.test(content)) content = `'${content}`;
      return `"${content.replace(/"/g, '""')}"`;
    };
    const lines = [columns.map(escape).join(',')];
    const append = (identity, values) => {
      const row = {...identity, ...values};
      lines.push(columns.map(key => escape(row[key])).join(','));
    };
    for (const series of state.filtered) {
      const identity = {...series, model: series.vehicle_model, series_id: series.id, demand_source: state.source, market: state.payload.market,
        currency: state.payload.currency, price_sources: (series.price_sources || []).join('|'), sensitivity_source: series.model.sensitivity_source};
      for (const point of series.history || []) {
        append(identity, {...point, horizon: 0, path: 'historical', rate: point.take_rate,
          price_source: 'artifact_catalogue_context_or_cohort_on_purchase_only_dates'});
        if (numeric(point.demand_price)) append(identity, {...point, horizon: 0, path: 'historical_demand_price',
          price: point.demand_price, rate: point.take_rate, price_source: (series.price_sources || []).join('|')});
      }
      append(identity, {date: series.as_of, horizon: 0, path: 'historical_full_period',
        price: series.latest_price, price_source: 'latest_catalogue_price_not_period_average', rate: series.take_rate});
      for (const [path, change] of [['unchanged', 0], ['scenario', state.change]]) {
        const endpoint = window.OptionIntelligenceModel.predictSeries(series, change, 2)[1];
        if (!endpoint) continue;
        for (const horizon of [30, 90]) {
          const date = new Date(`${series.model.last_date}T00:00:00Z`);
          date.setUTCDate(date.getUTCDate() + horizon);
          append(identity, {...endpoint, date: date.toISOString().slice(0, 10), horizon, path,
          price: series.model.last_price * (1 + change / 100), price_source: path === 'scenario' ? 'scenario' : 'unchanged_catalogue_reference',
          pct_change: change, eligible_count: null, selected_count: null,
          uncertainty_source: `${state.source}:approximate_model_prior_conditional_laplace_expected_rate`});
        }
      }
    }
    const blob = new Blob([lines.join('\r\n')], {type: 'text/csv;charset=utf-8'});
    const url = URL.createObjectURL(blob);
    const anchor = node('a');
    anchor.href = url;
    anchor.download = `option-intelligence-${state.payload.market}-${state.source}-${state.horizon}d.csv`;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function initialize() {
    if (state.initialized || !element('source')) return;
    state.initialized = true;
    element('source').addEventListener('change', event => { state.source = event.target.value; load(); });
    element('horizon').addEventListener('change', event => {
      state.horizon = Number(event.target.value);
      if (state.payload) { renderTable(); renderDetail(); }
    });
    element('retry').addEventListener('click', () => load(true));
    let searchTimer;
    element('search').addEventListener('input', () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(renderFiltered, 150);
    });
    element('series').addEventListener('change', event => { state.selected = event.target.value; renderTable(); renderDetail(); });
    element('sort').addEventListener('change', () => { state.page = 0; renderTable(); });
    element('prev').addEventListener('click', () => { state.page -= 1; renderTable(); });
    element('next').addEventListener('click', () => { state.page += 1; renderTable(); });
    element('change').addEventListener('input', event => scenarioInput(event.target.value));
    element('change-slider').addEventListener('input', event => scenarioInput(event.target.value, true));
    element('reset').addEventListener('click', () => scenarioInput('0', true));
    element('export').addEventListener('click', downloadCSV);
    load();
  }

  window.OptionIntelligence = {initialize, marketChanged: () => { if (state.initialized) load(); }, filtersChanged: renderFiltered};
})();