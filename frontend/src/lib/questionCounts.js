export function apportionCounts(values, total) {
  const entries = Object.entries(values);
  const sum = entries.reduce((n, [, value]) => n + Number(value), 0);
  if (!sum) return {};
  const exact = entries.map(([key, value]) => ({ key, exact: total * value / sum }));
  const result = Object.fromEntries(exact.map(({ key, exact: n }) => [key, Math.floor(n)]));
  const remainder = total - Object.values(result).reduce((n, value) => n + value, 0);
  exact.sort((a, b) => (b.exact - result[b.key]) - (a.exact - result[a.key]));
  exact.slice(0, remainder).forEach(({ key }) => { result[key] += 1; });
  return result;
}
