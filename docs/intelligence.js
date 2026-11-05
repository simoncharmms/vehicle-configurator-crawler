(() => {
  'use strict';

  const element = id => document.getElementById(`intelligence-${id}`);
  const PAGE_SIZE = 50;
  const TREND_MODEL = {
    minPoints: 6,
    shortWindow: 14,
    longWindow: 45,
    defaultProfile: {
      key: 'default',
      scenarioImpactPerPct: 0.45,
      halfLifeDays: 42,
      confidenceBonus: 0,
      featureScales: {
        dipDepth: 1,
        volatility: 1,
      },
      weights: {
        relative: 220,
        short: 120,
        long: 60,
        dipPenalty: 180,
        volatilityPenalty: 140,
      },
    },
    profiles: {
      alloy_wheels: {
        key: 'alloy_wheels',
        scenarioImpactPerPct: 0.55,
        halfLifeDays: 54,
        confidenceBonus: 0.08,
        featureScales: {
          dipDepth: 1.12,
          volatility: 0.55,
        },
        weights: {
          relative: 280,
          short: 150,
          long: 80,
          dipPenalty: 220,
          volatilityPenalty: 65,
        },
      },
    },
    interval: {
      base: 6,
      confidenceScale: 16,
      volatilityScale: 60,
    },
  };
  const cache = new Map();
  const detailCache = new Map();
  let detailRequest = 0;
  const state = {source: 'synthetic', horizon: 30, change: 0, page: 0, selected: null,
    payload: null, filtered: [], summaries: new Map(), features: new Map(), trendContext: null,
    request: 0, key: null, initialized: false, searchIndex: new Map()};
  let manifestPromise = null;
  let priceChart = null;
  let rateChart = null;
  const normalize = value => String(value || '').toLowerCase().replace(/[^a-z0-9]/g, '');
  const readable = value => String(value || 'unavailable').replace(/_/g, ' ');
  const numeric = value => typeof value === 'number' && Number.isFinite(value);
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
  const average = values => values.length ? values.reduce((total, value) => total + value, 0) / values.length : null;
  const median = values => {
    if (!values.length) return null;
    const ordered = [...values].sort((left, right) => left - right);
    const mid = Math.floor(ordered.length / 2);
    return ordered.length % 2 ? ordered[mid] : (ordered[mid - 1] + ordered[mid]) / 2;
  };
  const stddev = values => {
    if (values.length < 2) return 0;
    const mu = average(values);
    return Math.sqrt(values.reduce((total, value) => total + (value - mu) ** 2, 0) / values.length);
  };
  const safeLogRatio = (numerator, denominator) => {
    if (!numeric(numerator) || !numeric(denominator) || numerator <= 0 || denominator <= 0) return 0;
    return Math.log(numerator / denominator);
  };
  const isDate = value => /^\d{4}-\d{2}-\d{2}$/.test(String(value || ''));
  const addDays = (dateString, days) => {
    if (!isDate(dateString)) return null;
    const target = new Date(`${dateString}T00:00:00Z`);
    target.setUTCDate(target.getUTCDate() + days);
    return target.toISOString().slice(0, 10);
  };
  const count = value => numeric(value) ? value.toLocaleString() : 'Unavailable';
  const rate = value => numeric(value) ? `${(value * 100).toFixed(1)}%` : 'Unavailable';
  const points = value => numeric(value) ? `${value >= 0 ? '+' : ''}${(value * 100).toFixed(2)}` : 'Unavailable';
  const signedPercent = value => numeric(value) ? `${value >= 0 ? '+' : ''}${(value * 100).toFixed(2)}%` : 'Unavailable';
  const indexScale = value => numeric(value) ? `${value.toFixed(1)} / 100` : 'Unavailable';
  const indexDelta = value => numeric(value) ? `${value >= 0 ? '+' : ''}${value.toFixed(2)}` : 'Unavailable';
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
    state.features.clear();
    state.trendContext = null;
    state.page = 0;
    element('content').hidden = true;
    element('cohorts').hidden = true;
    clearCharts();
    text('source-label', sourceLabel());
    text('source-caveat', source === 'synthetic'
      ? 'Synthetic exposures, selections and eligibility are simulated, not actual sales. Trend-proxy strength is inferred from option price history, not observed take-rate labels.'
      : 'Observed purchases only. Trend-proxy strength is inferred from option price history, not direct per-option buyer labels.');
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
        status(`No observed purchase cohorts for ${market}. Catalogue series may still support trend-proxy strength scoring.`, 'empty');
      } else if (payload.status === 'no_usable_prices' || !payload.series.length) {
        status(`No usable option price series for ${market} in this source.`, 'empty');
      } else {
        status(`${sourceLabel()} / ${market} / ${payload.currency} / trend-proxy model / as of ${payload.as_of || 'unknown'} / published ${payload.generated_at || 'unknown'}`);
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
    state.trendContext = buildTrendContext(state.filtered);
    state.summaries.clear();
    state.page = 0;
    let eligible = 0;
    let selected = 0;
    for (const series of state.filtered) {
      eligible += series.eligible_count || 0;
      selected += series.selected_count || 0;
    }
    text('totals', `${sourceLabel()} / ${state.filtered.length.toLocaleString()} of ${state.payload.series.length.toLocaleString()} series / ${count(selected)} selected / ${count(eligible)} ${state.source === 'synthetic' ? 'simulated option exposures (not actual sales)' : 'option exposures (not unique buyers)'} / peer short-trend baseline ${signedPercent(state.trendContext?.marketShortMomentum)}.`);
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

  function historyPrices(series) {
    return (series.history || [])
      .filter(point => isDate(point.date) && numeric(point.price) && point.price > 0)
      .map(point => ({date: point.date, price: point.price}))
      .sort((left, right) => left.date.localeCompare(right.date));
  }

  function deriveHistoryFeatures(series) {
    const points = historyPrices(series);
    if (points.length < TREND_MODEL.minPoints) return null;
    const prices = points.map(point => point.price);
    const shortWindow = Math.max(3, Math.min(TREND_MODEL.shortWindow, Math.floor(points.length / 2)));
    if (points.length < shortWindow * 2) return null;
    const recent = prices.slice(-shortWindow);
    const previous = prices.slice(-shortWindow * 2, -shortWindow);
    const recentAvg = average(recent);
    const previousAvg = average(previous);
    const anchorIndex = Math.max(0, prices.length - Math.min(TREND_MODEL.longWindow, prices.length));
    const anchorPrice = prices[anchorIndex];
    const returns = [];
    for (let index = 1; index < prices.length; index += 1) {
      const move = safeLogRatio(prices[index], prices[index - 1]);
      if (numeric(move)) returns.push(move);
    }
    const recentMin = Math.min(...recent);
    return {
      available: true,
      basis: 'history_derived',
      shortMomentum: safeLogRatio(recentAvg, previousAvg),
      longMomentum: safeLogRatio(prices.at(-1), anchorPrice),
      dipDepth: Math.max(0, safeLogRatio(previousAvg, recentMin)),
      volatility: clamp(stddev(returns.slice(-21)), 0, 0.35),
      observedDays: points.length,
      priceLevels: new Set(prices.map(value => value.toFixed(2))).size,
      anchorDate: points.at(-1)?.date || null,
      latestPrice: prices.at(-1),
    };
  }

  function deriveSummaryFeatures(series) {
    const latestPrice = numeric(series.latest_price) ? series.latest_price
      : numeric(series.model?.last_price) ? series.model.last_price
        : null;
    if (!numeric(latestPrice) || latestPrice <= 0) {
      return {available: false, reason: 'missing_latest_price'};
    }
    const reference = numeric(series.model?.reference_price) && series.model.reference_price > 0
      ? series.model.reference_price
      : latestPrice;
    const coverage = series.coverage || {};
    const observedDays = coverage.observed_days || series.model?.training_days || 0;
    const priceLevels = coverage.different_price_levels || Math.max(1, Math.round(observedDays * 0.2));
    const changes = coverage.price_changes || 0;
    const changeRate = changes / Math.max(1, observedDays - 1);
    const levelDensity = priceLevels / Math.max(1, observedDays || 1);
    const pct = numeric(series.price_change_pct) && series.price_change_pct > -100
      ? series.price_change_pct / 100
      : null;
    const longMomentum = pct == null ? safeLogRatio(latestPrice + 1, reference + 1) : Math.log1p(pct);
    return {
      available: true,
      basis: 'summary_derived',
      shortMomentum: safeLogRatio(latestPrice + 1, reference + 1),
      longMomentum,
      dipDepth: pct != null && pct < 0 ? Math.min(0.35, Math.abs(pct) * 0.4) : 0,
      volatility: clamp(changeRate * 0.22 + Math.max(0, levelDensity - 0.06), 0, 0.35),
      observedDays,
      priceLevels,
      anchorDate: series.coverage?.last_date || series.model?.last_date || series.as_of || state.payload?.as_of || null,
      latestPrice,
    };
  }

  function priceFeatures(series) {
    if (!series.history && state.features.has(series.id)) return state.features.get(series.id);
    const summary = deriveSummaryFeatures(series);
    const history = deriveHistoryFeatures(series);
    const merged = history ? {...summary, ...history} : summary;
    merged.available = Boolean(history?.available || summary.available);
    if (!series.history && merged.available) state.features.set(series.id, merged);
    return merged;
  }

  function buildTrendContext(seriesList) {
    const short = [];
    const long = [];
    const volatility = [];
    for (const series of seriesList) {
      const features = priceFeatures(series);
      if (!features.available) continue;
      if (numeric(features.shortMomentum)) short.push(features.shortMomentum);
      if (numeric(features.longMomentum)) long.push(features.longMomentum);
      if (numeric(features.volatility)) volatility.push(features.volatility);
    }
    return {
      marketShortMomentum: median(short) || 0,
      marketLongMomentum: median(long) || 0,
      marketVolatility: median(volatility) || 0,
      sampleCount: short.length,
    };
  }

  function isAlloyWheelSeries(series) {
    const category = String(series.category || '').toLowerCase();
    const corpus = [series.standardized_name, series.option_name, series.option_code]
      .filter(Boolean)
      .join(' ')
      .toLowerCase();
    if (corpus.includes('lenkrad') || corpus.includes('steering wheel')) return false;
    return category === 'wheels'
      || /\b(alloy|wheel|wheels|rim|rims|felge|felgen|leichtmetall)\b/i.test(corpus);
  }

  function profileForSeries(series) {
    if (isAlloyWheelSeries(series)) return TREND_MODEL.profiles.alloy_wheels;
    return TREND_MODEL.defaultProfile;
  }

  function signalFromSeries(series, context = state.trendContext) {
    const features = priceFeatures(series);
    if (!features.available || !isDate(features.anchorDate)) {
      return {available: false, reason: features.reason || 'insufficient_price_history'};
    }
    const profile = profileForSeries(series);
    const scaledDipDepth = features.dipDepth * profile.featureScales.dipDepth;
    const scaledVolatility = features.volatility * profile.featureScales.volatility;
    const marketShortMomentum = context?.marketShortMomentum || 0;
    const relativeMomentum = features.shortMomentum - marketShortMomentum;
    const components = {
      relative: profile.weights.relative * relativeMomentum,
      short: profile.weights.short * features.shortMomentum,
      long: profile.weights.long * features.longMomentum,
      dipPenalty: profile.weights.dipPenalty * scaledDipDepth,
      volatilityPenalty: profile.weights.volatilityPenalty * scaledVolatility,
    };
    const baselineIndex = clamp(
      50 + components.relative + components.short + components.long - components.dipPenalty - components.volatilityPenalty,
      1,
      99,
    );
    const confidence = clamp(
      0.22 + Math.min(features.observedDays || 0, 90) / 120 + Math.min(features.priceLevels || 0, 12) / 24
      - scaledVolatility * 1.8 + profile.confidenceBonus,
      0.2,
      0.95,
    );
    const intervalWidth = TREND_MODEL.interval.base
      + (1 - confidence) * TREND_MODEL.interval.confidenceScale
      + scaledVolatility * TREND_MODEL.interval.volatilityScale;
    return {
      available: true,
      ...features,
      marketShortMomentum,
      relativeMomentum,
      scaledDipDepth,
      scaledVolatility,
      components,
      profileKey: profile.key,
      profileLabel: readable(profile.key),
      weights: profile.weights,
      scenarioImpactPerPct: profile.scenarioImpactPerPct,
      halfLifeDays: profile.halfLifeDays,
      baselineIndex,
      confidence,
      intervalWidth,
      anchorDate: features.anchorDate,
    };
  }

  function projectedIndex(start, day, halfLifeDays) {
    const decay = Math.exp(-(day - 1) / halfLifeDays);
    return clamp(50 + (start - 50) * decay, 1, 99);
  }

  function projectPoint(signal, changePct, day) {
    const start = clamp(signal.baselineIndex - changePct * signal.scenarioImpactPerPct, 1, 99);
    const index = projectedIndex(start, day, signal.halfLifeDays);
    const width = signal.intervalWidth * Math.sqrt(1 + (day - 1) / 60);
    return {
      index,
      rate: index / 100,
      lower: clamp(index - width, 1, 99) / 100,
      upper: clamp(index + width, 1, 99) / 100,
      start,
    };
  }

  function projectSeries(signal, changePct, horizon) {
    if (!signal.available) return [];
    const points = [];
    for (let day = 1; day <= horizon; day += 1) {
      const date = addDays(signal.anchorDate, day);
      if (!date) break;
      points.push({date, day, ...projectPoint(signal, changePct, day)});
    }
    return points;
  }

  function historicalSignalTimeline(series) {
    const history = historyPrices(series);
    if (history.length < TREND_MODEL.minPoints) return [];
    const timeline = [];
    for (let index = TREND_MODEL.minPoints - 1; index < history.length; index += 1) {
      const partial = {...series, history: history.slice(0, index + 1)};
      const signal = signalFromSeries(partial, state.trendContext);
      if (signal.available) timeline.push({date: history[index].date, rate: signal.baselineIndex / 100});
    }
    return timeline;
  }

  function forecastSummary(series) {
    if (!state.summaries.has(series.id)) {
      const signal = signalFromSeries(series);
      if (!signal.available) {
        state.summaries.set(series.id, {
          signal,
          currentStrength: null,
          baseline30: null,
          baseline90: null,
          scenario30: null,
          scenario90: null,
          confidence: null,
          basis: 'insufficient_data',
          observedDays: null,
        });
      } else {
        state.summaries.set(series.id, {
          signal,
          currentStrength: signal.baselineIndex / 100,
          baseline30: projectPoint(signal, 0, 30).rate,
          baseline90: projectPoint(signal, 0, 90).rate,
          scenario30: projectPoint(signal, state.change, 30).rate,
          scenario90: projectPoint(signal, state.change, 90).rate,
          confidence: signal.confidence,
          basis: signal.basis,
          observedDays: signal.observedDays,
        });
      }
    }
    return state.summaries.get(series.id);
  }

  function renderTable() {
    const sort = element('sort').value;
    const value = series => {
      if (sort === 'take_rate') return forecastSummary(series).currentStrength;
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
      for (const content of [money(series.latest_price),
        numeric(summary.confidence) ? `${(summary.confidence * 100).toFixed(0)}% / ${count(summary.observedDays)}` : 'Unavailable',
        rate(summary.currentStrength), rate(summary.scenario30), rate(summary.scenario90),
        points(numeric(expected) && numeric(baseline) ? expected - baseline : null),
        `${coverage.observed_days || 0} / ${coverage.calendar_span_days || 0} days; ${coverage.different_price_levels || 0} price levels`,
        readable(summary.basis)]) row.append(node('td', content));
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
    text('table-label', `${sourceLabel()} / trend-proxy strength uses relative price momentum, dip depth and price stability. Scenario delta shows the ${state.change}% price shock impact at ${state.horizon} days; this is directional, not causal proof.`);
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
      text('scenario-status', 'Loading selected option price history and trend-signal details...');
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
    const signal = signalFromSeries(series, state.trendContext);
    if (!signal.available) {
      text('scenario-status', 'Trend signal unavailable: insufficient usable price history for this option series.');
      element('model-status').replaceChildren();
      element('warnings').replaceChildren();
      element('metrics').replaceChildren();
      return;
    }
    const baseline = projectSeries(signal, 0, state.horizon);
    const scenario = state.change === 0 ? baseline : projectSeries(signal, state.change, state.horizon);
    text('series-title', `${sourceLabel()} / ${label(series)}`);
    const proposed = numeric(signal.latestPrice) ? signal.latestPrice * (1 + state.change / 100) : null;
    text('scenario-status', baseline.length
      ? `Price-trend take-rate proxy (not observed buyer labels). Peer baseline short trend: ${signedPercent(signal.marketShortMomentum)}. Current strength ${rate(signal.baselineIndex / 100)}. Day ${state.horizon}: unchanged ${rate(baseline.at(-1)?.rate)}, scenario ${rate(scenario.at(-1)?.rate)}; ${points(scenario.at(-1).rate - baseline.at(-1).rate)} pp.`
      : 'Projection unavailable: insufficient valid anchor date or horizon data.');
    const scenarioShift = -state.change * signal.scenarioImpactPerPct;
    const facts = [
      ['Model lens', 'Price-trend take-rate proxy (heuristic)'],
      ['Applied profile', signal.profileLabel],
      ['Recent momentum (short window)', signedPercent(signal.shortMomentum)],
      ['Peer-market momentum baseline', signedPercent(signal.marketShortMomentum)],
      ['Relative momentum', signedPercent(signal.relativeMomentum)],
      ['Dip pressure / volatility', `${signedPercent(-signal.dipDepth)} / ${signedPercent(signal.volatility)}`],
      ['Signal confidence', `${(signal.confidence * 100).toFixed(0)}%`],
      ['Anchor date / latest price', `${signal.anchorDate} / ${money(signal.latestPrice)}`],
      ['Scenario shock contribution', `${indexDelta(scenarioShift)} index points (${state.change}%)`],
      ['Data basis', readable(signal.basis)],
      ['Series identity', series.id],
    ];
    element('model-status').replaceChildren(...facts.map(([name, value]) => {
      const group = node('div');
      group.append(node('dt', name), node('dd', value));
      return group;
    }));
    const warningSet = new Set(series.warnings || []);
    if (signal.basis !== 'history_derived') warningSet.add('detail_history_missing_summary_fallback_used');
    if (signal.confidence < 0.45) warningSet.add('low_signal_confidence_directional_interpretation_only');
    element('warnings').replaceChildren(...[...warningSet].map(warning => node('li', readable(warning))));
    renderMetrics(signal);
    renderCharts(series, signal, baseline, scenario);
  }

  function renderMetrics(signal) {
    const scenarioContribution = -state.change * signal.scenarioImpactPerPct;
    const rows = [
      ['Relative momentum', signedPercent(signal.relativeMomentum), `+${signal.weights.relative.toFixed(0)}`, indexDelta(signal.components.relative),
        signal.components.relative >= 0 ? 'Supports stronger take-rate signal' : 'Weakens signal', 'Peer-normalized short trend', 'Compares option trend vs market median trend.'],
      ['Short momentum', signedPercent(signal.shortMomentum), `+${signal.weights.short.toFixed(0)}`, indexDelta(signal.components.short),
        signal.components.short >= 0 ? 'Supports strength' : 'Signals discount pressure', readable(signal.basis), 'Uses latest window vs prior window price average.'],
      ['Long momentum', signedPercent(signal.longMomentum), `+${signal.weights.long.toFixed(0)}`, indexDelta(signal.components.long),
        signal.components.long >= 0 ? 'Supports sustained strength' : 'Weakens sustained signal', readable(signal.basis), 'Captures broader directional drift.'],
      ['Dip penalty', `${signedPercent(signal.dipDepth)} (scaled ${signedPercent(signal.scaledDipDepth)})`, `-${signal.weights.dipPenalty.toFixed(0)}`, indexDelta(-signal.components.dipPenalty),
        'Penalty for recent discount depth', readable(signal.basis), 'Deeper dips imply push to stimulate weaker demand.'],
      ['Volatility penalty', `${signedPercent(signal.volatility)} (scaled ${signedPercent(signal.scaledVolatility)})`, `-${signal.weights.volatilityPenalty.toFixed(0)}`, indexDelta(-signal.components.volatilityPenalty),
        'Penalty for unstable pricing', readable(signal.basis), 'Frequent/large shifts reduce confidence in organic pull.'],
      ['Scenario shock', `${state.change}%`, `-${signal.scenarioImpactPerPct.toFixed(2)} / pct`, indexDelta(scenarioContribution),
        scenarioContribution >= 0 ? 'Price cut strengthens signal' : 'Price increase weakens signal', 'Scenario control', 'Applies once, then mean-reverts toward neutral.'],
      ['Baseline index', indexScale(signal.baselineIndex), 'Neutral anchor 50', indexDelta(signal.baselineIndex - 50),
        signal.baselineIndex >= 50 ? 'Above-neutral strength proxy' : 'Below-neutral strength proxy', `${(signal.confidence * 100).toFixed(0)}% confidence`,
        'Directional estimate only; not a causal estimate of true take rate.'],
    ];
    element('metrics').replaceChildren(...rows.map(columns => {
      const row = node('tr');
      for (const content of columns) row.append(node('td', content));
      return row;
    }));
    text('metrics-title', `${sourceLabel()} / trend-signal decomposition`);
    text('evaluation-dates',
      `Anchor date ${signal.anchorDate}; observed price points ${count(signal.observedDays)}; distinct price levels ${count(signal.priceLevels)}; peer sample ${count(state.trendContext?.sampleCount)}.`);
  }

  function renderCharts(series, signal, baseline, scenario) {
    if (typeof Chart === 'undefined') {
      text('scenario-status', `${element('scenario-status').textContent} Charts unavailable: Chart.js did not load.`);
      return;
    }
    const history = historyPrices(series);
    const historyAverage = new Map();
    for (let index = 0; index < history.length; index += 1) {
      const start = Math.max(0, index - 6);
      const avg = average(history.slice(start, index + 1).map(point => point.price));
      historyAverage.set(history[index].date, avg);
    }
    const historySignal = new Map(historicalSignalTimeline(series).map(point => [point.date, point.rate]));
    const dates = [...new Set([...history.map(point => point.date), ...scenario.map(point => point.date)])].sort();
    const historical = new Map(history.map(point => [point.date, point.price]));
    const unchanged = new Map(baseline.map(point => [point.date, point]));
    const changed = new Map(scenario.map(point => [point.date, point]));
    const unchangedPrice = new Map(baseline.map(point => [point.date, signal.latestPrice]));
    const scenarioPrice = numeric(signal.latestPrice)
      ? new Map(scenario.map(point => [point.date, signal.latestPrice * (1 + state.change / 100)]))
      : new Map();
    const css = getComputedStyle(document.documentElement);
    const accent = css.getPropertyValue('--accent').trim();
    const success = css.getPropertyValue('--success').trim();
    const warning = css.getPropertyValue('--warning').trim();
    const numberData = mapping => dates.map(day => {
      const value = mapping.get(day);
      return numeric(value) ? value : null;
    });
    const objectData = (mapping, key, multiplier = 1) => dates.map(day => {
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
    text('price-caption', `${sourceLabel()} / ${state.payload.currency} / observed option price, rolling baseline and scenario path`);
    text('rate-caption', `${sourceLabel()} / trend-proxy take-rate strength / ${state.horizon}-day directional projection`);
    priceChart = new Chart(element('price-chart'), {type: 'line', data: {labels: dates, datasets: [
      line('Observed option price', numberData(historical), accent),
      line('7-point rolling average', numberData(historyAverage), success),
      line('Unchanged scenario price', numberData(unchangedPrice), accent, true),
      line('Price scenario path', numberData(scenarioPrice), warning, true),
    ]}, options: options(false)});
    const interval = (mapping, color, name) => [
      {label: '', data: objectData(mapping, 'lower', 100), borderWidth: 0, pointRadius: 0, backgroundColor: 'transparent'},
      {label: `${name} directional interval`, data: objectData(mapping, 'upper', 100),
        borderWidth: 0, pointRadius: 0, fill: '-1', backgroundColor: color},
    ];
    rateChart = new Chart(element('rate-chart'), {type: 'line', data: {labels: dates, datasets: [
      ...interval(unchanged, 'rgba(79,143,247,0.12)', 'Unchanged'),
      ...interval(changed, 'rgba(255,152,0,0.12)', 'Scenario'),
      line('Historical trend strength proxy', numberData(new Map([...historySignal.entries()].map(([date, value]) => [date, value * 100]))), success),
      line('Unchanged expected strength', objectData(unchanged, 'rate', 100), accent, true),
      line('Scenario expected strength', objectData(changed, 'rate', 100), warning, true),
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
      const summary = forecastSummary(series);
      const signal = summary.signal;
      const identity = {...series, model: series.vehicle_model, series_id: series.id, demand_source: state.source, market: state.payload.market,
        currency: state.payload.currency, price_sources: (series.price_sources || []).join('|'),
        sensitivity_source: signal?.basis || readable(series.model?.sensitivity_source)};
      for (const point of series.history || []) {
        append(identity, {...point, horizon: 0, path: 'historical_price', rate: null,
          price_source: 'catalogue_history'});
      }
      append(identity, {date: series.as_of, horizon: 0, path: 'historical_full_period',
        price: series.latest_price, price_source: 'latest_catalogue_price_not_period_average', rate: summary.currentStrength});
      if (!signal?.available) continue;
      for (const [path, change] of [['unchanged', 0], ['scenario', state.change]]) {
        for (const horizon of [30, 90]) {
          const endpoint = projectPoint(signal, change, horizon);
          append(identity, {...endpoint, date: addDays(signal.anchorDate, horizon), horizon, path,
          price: signal.latestPrice * (1 + change / 100), price_source: path === 'scenario' ? 'scenario' : 'unchanged_catalogue_reference',
          pct_change: change, eligible_count: null, selected_count: null,
          uncertainty_source: `${state.source}:trend_proxy_price_signal_v1`});
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