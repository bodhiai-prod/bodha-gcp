import React, { useEffect, useState } from 'react';
import { api } from '@/lib/api';

const money = value => new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 4 }).format(value);
const duration = value => value == null ? '—' : `${(value / 1000).toFixed(1)}s`;
const label = value => value.replaceAll('_', ' ');
const category = kind => ({ paper_job: 'Generation', question_generation: 'Generation', completion: 'Completion', template_recovery: 'Completion', choice_repair: 'Completion', solution: 'Solution', regeneration: 'Regeneration', diagram: 'Diagrams', ingestion: 'Ingestion', book_indexing: 'Indexing' }[kind] || label(kind));

export default function AdminUsage() {
  const [filters, setFilters] = useState({ kind: '', status: '', resource_id: '' });
  const [page, setPage] = useState(1);
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [backfilling, setBackfilling] = useState(false);
  const [backfillReport, setBackfillReport] = useState('');
  const [selection, setSelection] = useState({});
  const backfill = async () => {
    setBackfilling(true); setError('');
    try {
      const { data: result } = await api.post('/admin/usage/backfill');
      setBackfillReport(`Recovered ${result.operations_created} historical groups and ${result.calls_imported} provider calls. ${result.csv_available ? 'Available token logs were scanned.' : 'No token CSV is available on this server; costs and call details cannot be recovered from resource records alone.'} ${result.malformed_rows || 0} malformed log rows skipped.`);
      setPage(1); setRefresh(value => value + 1);
    } catch { setError('Historical import failed. Retry is safe; already imported entries are retained.'); }
    finally { setBackfilling(false); }
  };
  useEffect(() => {
    let active = true;
    setLoading(true); setError('');
    api.get('/admin/usage', { params: { ...filters, page } }).then(({ data: result }) => {
      if (active) setData(result);
    }).catch(() => { if (active) setError('Usage could not be loaded. Please retry.'); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [filters, page, refresh]);
  const change = (key, value) => { setFilters(previous => ({ ...previous, [key]: value })); setPage(1); };
  const groups = Object.values((data?.items || []).reduce((result, operation) => {
    const key = `${operation.resource_type}:${operation.resource_id}`;
    if (!result[key]) result[key] = { key, operations: [] };
    result[key].operations.push(operation);
    return result;
  }, {}));
  return <section className="space-y-6" aria-label="Usage and reliability">
    <div className="flex flex-wrap justify-between gap-4">
      <div><h2 className="font-display text-3xl">Usage & reliability</h2><p className="text-sm text-neutral-500 mt-2">Trace provider spend from each resource to its operations and individual calls.</p></div>
      <div className="flex gap-3"><button disabled={backfilling} className="border-2 border-black px-4 py-2 bg-white disabled:opacity-50" onClick={backfill}>{backfilling ? 'Recovering history…' : 'Recover previous generations'}</button><button className="border-2 border-black px-4 py-2 bg-white" onClick={() => setRefresh(value => value + 1)}>Refresh</button></div>
    </div>
    <div className="border-l-4 border-[#002FA7] bg-blue-50 p-4 text-sm">INR costs are estimates based on the pricing and exchange rate recorded with each call. Unknown charges are excluded from known subtotals. Provider call time can overlap and is not operation elapsed time.</div>
    <div className="flex flex-wrap gap-3">
      <select aria-label="Operation type" className="border border-neutral-300 bg-white p-3" value={filters.kind} onChange={event => change('kind', event.target.value)}>
        <option value="">All operations</option>{['paper_job', 'question_generation', 'solution', 'completion', 'regeneration', 'choice_repair', 'ingestion', 'book_indexing', 'diagram', 'template_recovery'].map(kind => <option key={kind} value={kind}>{label(kind)}</option>)}
      </select>
      <select aria-label="Outcome" className="border border-neutral-300 bg-white p-3" value={filters.status} onChange={event => change('status', event.target.value)}>
        <option value="">All outcomes</option>{['running', 'completed', 'partial', 'failed', 'extraction_failed', 'cancelled'].map(status => <option key={status}>{status}</option>)}
      </select>
      <input aria-label="Resource ID" placeholder="Filter by paper or book ID" className="border border-neutral-300 p-3 flex-1 min-w-48" value={filters.resource_id} onChange={event => change('resource_id', event.target.value)} />
    </div>
    {error && <div role="alert" className="bg-red-50 p-4 text-red-800">{error}</div>}
    {backfillReport && <p role="status" className="bg-blue-50 p-4 text-sm">{backfillReport}</p>}
    {loading ? <p role="status">Loading usage…</p> : data?.items.length === 0 ? <div className="border-2 border-dashed border-neutral-300 p-12 text-center"><h3 className="text-lg">No operations recorded</h3><p className="text-sm text-neutral-500 mt-2">New generation and ingestion runs will appear here. Try adjusting your filters.</p></div> : groups.map(group => {
      const first = group.operations[0];
      const categories = [...new Set(group.operations.map(operation => category(operation.kind)))];
      const selected = categories.includes(selection[group.key]) ? selection[group.key] : categories[0];
      const calls = group.operations.flatMap(operation => operation.calls);
      const known = calls.filter(call => call.cost_inr != null);
      return <section key={group.key} className="border-2 border-black bg-white" aria-label={`${first.resource_name} usage`}>
        <div className="p-5 flex flex-wrap justify-between gap-4"><div><p className="text-xs uppercase tracking-wider text-[#002FA7]">{first.resource_type}</p><h3 className="font-medium mt-1 break-all">{first.resource_name}</h3><p className="text-xs text-neutral-500 mt-1">{first.teacher}</p></div><div className="text-sm"><p className="text-xs text-neutral-500">{filters.kind || filters.status ? 'Known total for filtered operations' : 'Known total'}</p><p>{known.length ? money(known.reduce((sum, call) => sum + call.cost_inr, 0)) : 'Unavailable'}</p>{calls.length > known.length && <p className="text-xs text-amber-700">{calls.length - known.length} unknown charges</p>}</div></div>
        <div className="flex flex-wrap gap-2 border-t border-neutral-200 px-5 py-4" role="group" aria-label={`Operation type for ${first.resource_name}`}>{categories.map(option => <button key={option} type="button" aria-pressed={selected === option} onClick={() => setSelection(previous => ({ ...previous, [group.key]: option }))} className={`px-3 py-2 text-sm border ${selected === option ? 'bg-black text-white border-black' : 'bg-white border-neutral-300'}`}>{option} <span className="opacity-70">({group.operations.filter(operation => category(operation.kind) === option).length})</span></button>)}</div>
        <div className="px-5 pb-5 space-y-3">{group.operations.filter(operation => category(operation.kind) === selected).map(operation => <details key={operation.id} className="border border-neutral-300 bg-white group">
      <summary className="cursor-pointer p-5 flex flex-wrap items-center justify-between gap-4">
        <div className="min-w-0"><span className="text-xs uppercase tracking-wider text-[#002FA7]">{operation.resource_type} · {label(operation.kind)}</span><h3 className="font-medium mt-1 break-all">{operation.resource_name}</h3><p className="text-xs text-neutral-500 mt-1">{operation.teacher} · {new Date(operation.started_at).toLocaleString()}</p></div>
        <div className="flex flex-wrap gap-6 text-sm"><div><p className="text-xs text-neutral-500">Known subtotal</p><p>{operation.calls.length === 0 || operation.unknown_calls === operation.calls.length ? 'Unavailable' : money(operation.known_cost_inr)}</p>{operation.unknown_calls > 0 && <p className="text-xs text-amber-700">{operation.unknown_calls} unknown charges</p>}</div><div><p className="text-xs text-neutral-500">Operation elapsed</p>{duration(operation.active_ms)}</div><span className={operation.status.includes('fail') ? 'text-red-700' : 'text-neutral-600'}>{operation.status}</span></div>
      </summary>
      <div className="border-t border-neutral-200 p-5 space-y-4">
        {operation.source === 'historical' && <div className="bg-amber-50 p-3 text-sm"><strong>Recovered history</strong><p>{operation.history_note}</p><p>INR amounts, when available, are reconstructed using configured pricing at import time.</p></div>}
        <div className="text-xs text-neutral-500 break-all">Resource: <button className="underline text-[#002FA7]" onClick={() => change('resource_id', operation.resource_id)}>{operation.resource_id}</button><br />Operation: {operation.id}{operation.parent_id && <><br />Parent operation: {operation.parent_id}</>}</div>
        {operation.error_type && <p className="text-red-700 text-sm">Failure: {operation.error_type}</p>}
        {operation.book_cost && <div className="bg-blue-50 p-4 text-sm"><strong>Book lifetime cost breakdown</strong><p>Cloud Vision OCR: {money(operation.book_cost.vision_inr)} · LLM indexing and other calls: {money(operation.book_cost.llm_inr)} · Known total: {money(operation.book_cost.vision_inr + operation.book_cost.llm_inr)}</p><p>{operation.book_cost.unknown} unknown charges. Vision is a list-price estimate before free tier or volume discounts.</p></div>}
        {operation.job_id && <p className="text-sm">Queue job: {operation.job_id} · Attempt: {operation.attempt ?? '—'} · Revision: {operation.revision ?? '—'} · Queue wait: {duration(operation.queue_wait_ms)}</p>}
        {operation.stages?.length > 0 && <div><h4 className="font-medium text-sm mb-2">Processing stages</h4>{operation.stages.map(item => <div className="text-sm flex justify-between py-1" key={item.id}><span>{label(item.name)} · {item.status}</span><span>{duration(item.duration_ms)}</span></div>)}</div>}
        <p className="text-sm">{operation.calls.length} provider calls · {operation.tokens.toLocaleString('en-IN')} tokens. Expand a call to inspect its retry and provider details.</p>
        {!operation.calls.length && <p className="text-sm text-neutral-500">No provider calls recorded. This does not establish a zero cost.</p>}
        <div className="overflow-x-auto"><table className="w-full text-sm text-left"><thead className="text-xs uppercase text-neutral-500"><tr>{['Stage / model', 'Input tokens', 'Output tokens', 'Call latency', 'INR cost'].map(title => <th className="p-3 border-b" key={title}>{title}</th>)}</tr></thead><tbody>{operation.calls.map((call, index) => <React.Fragment key={call.id}><tr><td className="p-3"><details><summary className="cursor-pointer">{index + 1}. {label(call.kind)} · {call.model_id}</summary><div className="text-xs text-neutral-500 space-y-1 mt-2 break-all"><p>{call.id} · {call.outcome} · {call.stop_reason || "no stop reason"}</p>{call.request_group && <p>Request {call.request_group} · Provider attempt {call.provider_attempt} · Fallback {call.fallback_index}</p>}{call.work_phase && <p>{call.work_phase} · Round {call.generation_round ?? "—"} · Attempt {call.generation_attempt ?? "—"} · Batch {call.batch ?? "—"}</p>}<p>{new Date(call.timestamp).toLocaleString()}</p>{call.pricing && <><p>USD / million tokens: input {call.pricing.input_per_million ?? 0}, output {call.pricing.output_per_million ?? 0}</p><p>USD / image: {call.pricing.per_image ?? 0} · USD/INR: {call.fx_usd_inr}</p></>}</div></details></td><td className="p-3">{call.input_tokens}</td><td className="p-3">{call.output_tokens}</td><td className="p-3">{duration(call.latency_ms)}</td><td className="p-3">{call.cost_inr == null ? 'Unknown' : `${money(call.cost_inr)} estimated`}</td></tr></React.Fragment>)}</tbody></table></div>
      </div>
    </details>)}</div></section>;
    })}
    {data && <div className="flex items-center justify-between text-sm"><span>{data.total} papers and books</span><div className="flex items-center gap-4"><button disabled={page === 1 || loading} onClick={() => setPage(value => value - 1)} className="disabled:opacity-30">Previous</button><span>Page {page}</span><button disabled={page * data.page_size >= data.total || loading} onClick={() => setPage(value => value + 1)} className="disabled:opacity-30">Next</button></div></div>}
  </section>;
}
