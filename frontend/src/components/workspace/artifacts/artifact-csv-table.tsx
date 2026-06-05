import { useMemo } from "react";

/** Max data rows rendered into the DOM. Keeps large CSVs from freezing the UI. */
const MAX_TABLE_ROWS = 200;

/**
 * Minimal RFC-4180-ish CSV parser: handles quoted fields, embedded commas /
 * newlines, and escaped quotes (""). Good enough for previewing tabular data;
 * not a full streaming parser. Stops after `maxRows` data rows so we never walk
 * the entire string for a huge file.
 */
function parseCsv(text: string, maxRows: number): string[][] {
  const rows: string[][] = [];
  let field = "";
  let row: string[] = [];
  let inQuotes = false;

  const pushField = () => {
    row.push(field);
    field = "";
  };
  const pushRow = () => {
    pushField();
    rows.push(row);
    row = [];
  };

  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inQuotes) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        field += ch;
      }
      continue;
    }
    if (ch === '"') {
      inQuotes = true;
    } else if (ch === ",") {
      pushField();
    } else if (ch === "\n") {
      pushRow();
      // header + maxRows data rows
      if (rows.length > maxRows) break;
    } else if (ch !== "\r") {
      field += ch;
    }
  }
  // flush trailing field/row (skip a stray empty trailing line)
  if (field.length > 0 || row.length > 0) {
    pushRow();
  }
  return rows;
}

export function ArtifactCsvTable({
  content,
  truncated = false,
}: {
  content: string;
  /** Upstream content was already byte-capped, so the last row may be partial. */
  truncated?: boolean;
}) {
  const { header, body, hadMore } = useMemo(() => {
    const rows = parseCsv(content ?? "", MAX_TABLE_ROWS + 1);
    if (rows.length === 0) {
      return { header: [] as string[], body: [] as string[][], hadMore: false };
    }
    // If the source was byte-truncated, the final parsed row may be incomplete.
    if (truncated && rows.length > 1) {
      rows.pop();
    }
    const head = rows[0] ?? [];
    const rest = rows.slice(1);
    const hadMore = rest.length > MAX_TABLE_ROWS;
    return { header: head, body: rest.slice(0, MAX_TABLE_ROWS), hadMore };
  }, [content, truncated]);

  if (header.length === 0) {
    return (
      <div className="text-muted-foreground p-4 text-sm">
        No rows to display.
      </div>
    );
  }

  return (
    <div className="size-full overflow-auto">
      <table className="w-full border-collapse text-left text-xs">
        <thead className="bg-muted sticky top-0">
          <tr>
            <th className="text-muted-foreground border-b px-2 py-1 font-medium">
              #
            </th>
            {header.map((cell, i) => (
              <th
                key={i}
                className="border-b px-2 py-1 font-medium whitespace-nowrap"
                title={cell}
              >
                {cell}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {body.map((cells, r) => (
            <tr key={r} className="even:bg-muted/40">
              <td className="text-muted-foreground border-b px-2 py-1 tabular-nums">
                {r + 1}
              </td>
              {header.map((_, c) => (
                <td
                  key={c}
                  className="max-w-[24rem] truncate border-b px-2 py-1"
                  title={cells[c] ?? ""}
                >
                  {cells[c] ?? ""}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {(hadMore || truncated) && (
        <div className="text-muted-foreground bg-muted/30 px-2 py-1.5 text-xs">
          Showing first {body.length} rows. Download the file to view all rows.
        </div>
      )}
    </div>
  );
}
