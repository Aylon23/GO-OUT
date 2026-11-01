/** Split text into [before, match, after] parts for case-insensitive highlighting. */
export function splitMatch(text: string, query: string): { text: string; match: boolean }[] {
  const q = query.trim();
  if (!q) return [{ text, match: false }];
  const i = text.toLowerCase().indexOf(q.toLowerCase());
  if (i < 0) return [{ text, match: false }];
  return [
    { text: text.slice(0, i), match: false },
    { text: text.slice(i, i + q.length), match: true },
    { text: text.slice(i + q.length), match: false },
  ].filter((p) => p.text);
}
