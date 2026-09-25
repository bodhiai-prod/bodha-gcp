export const formatPercentage = (marks, totalMarks) => {
  if (!Number(totalMarks)) return 0;
  return Math.round((Number(marks || 0) / Number(totalMarks)) * 100);
};

export const getAvailableFormatMarks = (formats, key, totalMarks) => {
  const otherMarks = formats
    .filter((format) => format.key !== key)
    .reduce((sum, format) => sum + Number(format.marks || 0), 0);
  return Math.max(0, (Number(totalMarks) || 0) - otherMarks);
};

export const reconcileFormatMarks = (formats, totalMarks) => {
  let remaining = Math.max(0, Number(totalMarks) || 0);
  return formats.map((format) => {
    const marks = Math.min(Math.max(0, Number(format.marks) || 0), remaining);
    remaining -= marks;
    return { ...format, marks, value: formatPercentage(marks, totalMarks) };
  });
};
