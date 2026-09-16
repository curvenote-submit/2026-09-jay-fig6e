// Newick tree: parse, prune to a species set, ladderize, and lay out.
// Mirrors fig6e_core.tree_leaf_order / load_pruned_tree / _tree_node_coords
// (Biopython) so the row order matches the Python figure.

export interface TreeNode { name: string; length: number; children: TreeNode[]; _nt?: number; _y?: number }
export interface LayoutNode { x: number; y: number; name: string; tip: boolean }
export interface Edge { x0: number; y0: number; x1: number; y1: number }
export interface TreeLayout { order: string[]; nodes: LayoutNode[]; edges: Edge[]; depth: number }

/** Parse a Newick string into {name, length, children}. */
export function parseNewick(text: string): TreeNode {
  let i = 0;
  const s = text.trim();
  function node(): TreeNode {
    const n: TreeNode = { name: "", length: 0, children: [] };
    if (s[i] === "(") {
      i++;
      n.children.push(node());
      while (s[i] === ",") { i++; n.children.push(node()); }
      if (s[i] !== ")") throw new Error(`newick: expected ')' at ${i}`);
      i++;
    }
    let label = "";
    while (i < s.length && !",():;".includes(s[i])) label += s[i++];
    n.name = label;
    if (s[i] === ":") {
      i++;
      let num = "";
      while (i < s.length && !",();".includes(s[i])) num += s[i++];
      n.length = parseFloat(num) || 0;
    }
    return n;
  }
  const root = node();
  return root;
}

export function leaves(n: TreeNode, out: TreeNode[] = []): TreeNode[] {
  if (n.children.length === 0) out.push(n);
  else for (const c of n.children) leaves(c, out);
  return out;
}

function countTerminals(n: TreeNode): number {
  if (n._nt !== undefined) return n._nt;
  n._nt = n.children.length === 0 ? 1 : n.children.reduce((a, c) => a + countTerminals(c), 0);
  return n._nt;
}

/** Biopython-style ladderize(reverse=True): bigger clades first, recursively. */
export function ladderize(n: TreeNode): void {
  for (const c of n.children) ladderize(c);
  n.children.sort((a, b) => countTerminals(b) - countTerminals(a));
  // sort is stable, matching Python's list.sort on ties
}

/** Drop tips not in `keep`, collapsing unary internal nodes (Biopython prune). */
export function prune(n: TreeNode, keep: Set<string>): TreeNode | null {
  if (n.children.length === 0) return keep.has(n.name) ? n : null;
  const kids = n.children.map((c) => prune(c, keep)).filter((c): c is TreeNode => c !== null);
  if (kids.length === 0) return null;
  if (kids.length === 1) {
    // collapse: child inherits the summed branch length
    const c = kids[0];
    c.length += n.length;
    return c;
  }
  n.children = kids;
  n._nt = undefined;
  return n;
}

/**
 * Rectangular layout. Tips get y = 0..nTips-1 in leaf order; internal nodes
 * y = mean of children; x = cumulative branch length from the root.
 * Returns {order: [names], nodes: [{x, y, name, tip}], edges: [{x0,y0,x1,y1}]}.
 */
export function layout(root: TreeNode): TreeLayout {
  const nodes: LayoutNode[] = [], edges: Edge[] = [];
  let tipIdx = 0;
  function walk(n: TreeNode, x0: number): number {
    const x = x0 + n.length;
    let y: number;
    if (n.children.length === 0) {
      y = tipIdx++;
    } else {
      const ys = n.children.map((c) => walk(c, x));
      y = (Math.min(...ys) + Math.max(...ys)) / 2;
      // vertical connector spanning children, then horizontal to each child
      edges.push({ x0: x, y0: Math.min(...ys), x1: x, y1: Math.max(...ys) });
      for (const c of n.children) edges.push({ x0: x, y0: c._y!, x1: x + c.length, y1: c._y! });
    }
    n._y = y;
    nodes.push({ x, y, name: n.name, tip: n.children.length === 0 });
    return y;
  }
  walk(root, 0);
  const order = nodes.filter((n) => n.tip).sort((a, b) => a.y - b.y).map((n) => n.name);
  const depth = Math.max(...nodes.map((n) => n.x));
  return { order, nodes, edges, depth };
}

/** One-shot: newick text + retained species -> ladderized, pruned layout. */
export function buildTree(newick: string, retained: string[]): TreeLayout {
  const keep = new Set(retained);
  const root = prune(parseNewick(newick), keep);
  if (!root) return { order: [...retained], nodes: [], edges: [], depth: 0 };
  ladderize(root);
  const lay = layout(root);
  // species in `retained` but absent from the tree go at the bottom (Python does the same)
  const inTree = new Set(lay.order);
  lay.order.push(...retained.filter((s) => !inTree.has(s)));
  return lay;
}

export function abbreviate(name: string): string {
  const p = name.split("_");
  return p.length >= 2 ? `${p[0][0]}. ${p.slice(1).join(" ")}` : name;
}
