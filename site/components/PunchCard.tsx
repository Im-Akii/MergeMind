import { tokenPerms, type Access } from "@/lib/content";

const COLS: { level: Access; label: string }[] = [
  { level: "none", label: "No access" },
  { level: "read", label: "Read" },
  { level: "write", label: "Read & write" },
];

// The token's repository permissions, punched like a card: one hole per row.
export default function PunchCard() {
  return (
    <div className="punch">
      <table>
        <caption className="sr-only">Repository permissions for the MergeMind token</caption>
        <thead>
          <tr>
            <th scope="col">Permission</th>
            {COLS.map((c) => (
              <th key={c.level} scope="col">
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {tokenPerms.map((p) => (
            <tr key={p.name}>
              <td>
                <div className="perm-name">{p.name}</div>
                <div className="perm-note">{p.note}</div>
              </td>
              {COLS.map((c) => (
                <td key={c.level} className={`hole${p.level === c.level ? " on" : ""}`}>
                  <i />
                  {p.level === c.level && <span className="sr-only">selected</span>}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
