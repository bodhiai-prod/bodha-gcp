export const MAX_DIAGRAMS_PER_PAPER = 5;

export function countDiagramSelections(allocations) {
  return (allocations || []).filter((allocation) => Boolean(allocation.diagrams)).length;
}
