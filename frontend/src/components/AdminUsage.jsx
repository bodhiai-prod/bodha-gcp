import React, { useEffect, useState } from 'react';
import { api } from '@/lib/api';

const money = value => new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 4 }).format(value);
const duration = value => value == null ? '—' : `${(value / 1000).toFixed(1)}s`;
const label = value => value.replaceAll('_', ' ');

export default function AdminUsage() {
  const [filters, setFilters] = useState({ kind: '', status: '', resource_id: '' });
  const [page, setPage] = useState(1);
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [refresh, setRefresh] = useState(0);
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
  return <section className="space-y-6" aria-label="Usage and reliability">
    <div className="flex flex-wrap justify-between gap-4">
      <div><h2 className="font-display text-3xl">Usage & reliability</h2><p className="text-sm text-neutral-500 mt-2">Trace provider spend from each resource to its operations and individual calls.</p></div>
      <button className="border-2 border-black px-4 py-2 bg-white" onClick={() => setRefresh(value => value + 1)}>Refresh</button>
    </div>
    <div className="border-l-4 border-[#002FA7] bg-blue-50 p-4 text-sm">INR costs are estimates based on the pricing and exchange rate recorded with each call. Unknown charges are excluded from known subtotals. Provider call time can overlap and is not operation elapsed time.</div>
    <div className="flex flex-wrap gap-3">
      <select aria-label="Operation type" className="border border-neutral-300 bg-white p-3" value={filters.kind} onChange={event => change('kind', event.target.value)}>
        <option value="">All operations</option>{['question_generation', 'solution', 'completion', 'regeneration', 'choice_repair', 'ingestion'].map(kind => <option key={kind} value={kind}>{label(kind)}</option>)}
      </select>
      <select aria-label="Outcome" className="border border-neutral-300 bg-white p-3" value={filters.status} onChange={event => change('status', event.target.value)}>
        <option value="">All outcomes</option>{['running', 'completed', 'partial', 'failed', 'extraction_failed', 'cancelled'].map(status => <option key={status}>{status}</option>)}
      </select>
      <input aria-label="Resource ID" placeholder="Filter by paper or book ID" className="border border-neutral-300 p-3 flex-1 min-w-48" value={filters.resource_id} onChange={event => change('resource_id', event.target.value)} />
    </div>
    {error && <div role="alert" className="bg-red-50 p-4 text-red-800">{error}</div>}
    {loading ? <p role="status">Loading usage…</p> : data?.items.length === 0 ? <div className="border-2 border-dashed border-neutral-300 p-12 text-center"><h3 className="text-lg">No operations recorded</h3><p className="text-sm text-neutral-500 mt-2">New generation and ingestion runs will appear here. Try adjusting your filters.</p></div> : data?.items.map(operation => <details key={operation.id} className="border-2 border-black bg-white group">
      <summary className="cursor-pointer p-5 flex flex-wrap items-center justify-between gap-4">
        <div className="min-w-0"><span className="text-xs uppercase tracking-wider text-[#002FA7]">{operation.resource_type} · {label(operation.kind)}</span><h3 className="font-medium mt-1 break-all">{operation.resource_name}</h3><p className="text-xs text-neutral-500 mt-1">{operation.teacher} · {new Date(operation.started_at).toLocaleString()}</p></div>
        <div className="flex flex-wrap gap-6 text-sm"><div><p className="text-xs text-neutral-500">Known subtotal</p><p>{money(operation.known_cost_inr)}</p>{operation.unknown_calls > 0 && <p className="text-xs text-amber-700">+ {operation.unknown_calls} unknown charges</p>}</div><div><p className="text-xs text-neutral-500">Operation elapsed</p>{duration(operation.active_ms)}</div><span className={operation.status.includes('fail') ? 'text-red-700' : 'text-neutral-600'}>{operation.status}</span></div>
      </summary>
      <div className="border-t border-neutral-200 p-5 space-y-4">
        <div className="text-xs text-neutral-500 break-all">Resource: <button className="underline text-[#002FA7]" onClick={() => change('resource_id', operation.resource_id)}>{operation.resource_id}</button><br />Operation: {operation.id}{operation.parent_id && <><br />Parent operation: {operation.parent_id}</>}</div>
        {operation.error_type && <p className="text-red-700 text-sm">Failure: {operation.error_type}</p>}
        <p className="text-sm">{operation.calls.length} provider calls · {operation.tokens.toLocaleString('en-IN')} tokens</p>
        {!operation.calls.length && <p className="text-sm text-neutral-500">No provider calls recorded. This does not establish a zero cost.</p>}
        <div className="overflow-x-auto"><table className="w-full text-sm text-left"><thead className="text-xs uppercase text-neutral-500"><tr>{['Stage / model', 'Input tokens', 'Output tokens', 'Call latency', 'INR cost'].map(title => <th className="p-3 border-b" key={title}>{title}</th>)}</tr></thead><tbody>{operation.calls.map((call, index) => <React.Fragment key={call.id}><tr><td className="p-3"><details><summary className="cursor-pointer">{index + 1}. {label(call.kind)} · {call.model_id}</summary><div className="text-xs text-neutral-500 space-y-1 mt-2 break-all"><p>{call.id} · {call.outcome}</p><p>{new Date(call.timestamp).toLocaleString()}</p>{call.pricing && <><p>USD / million tokens: input {call.pricing.input_per_million ?? 0}, output {call.pricing.output_per_million ?? 0}</p><p>USD / image: {call.pricing.per_image ?? 0} · USD/INR: {call.fx_usd_inr}</p></>}</div></details></td><td className="p-3">{call.input_tokens}</td><td className="p-3">{call.output_tokens}</td><td className="p-3">{duration(call.latency_ms)}</td><td className="p-3">{call.cost_inr == null ? 'Unknown' : `${money(call.cost_inr)} estimated`}</td></tr></React.Fragment>)}</tbody></table></div>
      </div>
    </details>)}
    {data && <div className="flex items-center justify-between text-sm"><span>{data.total} operations</span><div className="flex items-center gap-4"><button disabled={page === 1 || loading} onClick={() => setPage(value => value - 1)} className="disabled:opacity-30">Previous</button><span>Page {page}</span><button disabled={page * data.page_size >= data.total || loading} onClick={() => setPage(value => value + 1)} className="disabled:opacity-30">Next</button></div></div>}
  </section>;
}
