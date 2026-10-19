import { useEffect, useState } from "react";

import { api } from "../api";
import { useDesk } from "../desk";
import { money } from "../format";
import { DataTable, Metrics, PageHead } from "../ui";

type Product = {
  sku: string;
  product_name_en: string | null;
  product_name_native: string | null;
  brand_name: string;
  product_status: string;
  category: string | null;
  subcategory: string | null;
  unit_size: string | null;
  variant: string | null;
  form: string | null;
  case_pack: number | null;
  case_price: number | null;
  moq_units: number | null;
  cases_per_layer: number | null;
  layers_per_pallet: number | null;
  cases_per_pallet: number | null;
  case_weight: number | null;
  policy_tags: string[];
  restricted_territories: string[];
  description_en: string | null;
};

type Catalog = {
  items: Product[];
  total: number;
  page: number;
  page_size: number;
  stats: { by_status: Record<string, number>; brands: number; categories: number };
};

const PAGE_SIZE = 100;

export function Products() {
  const { meta } = useDesk();
  const [q, setQ] = useState("");
  const [brand, setBrand] = useState("");
  const [category, setCategory] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [categories, setCategories] = useState<string[]>([]);
  const [data, setData] = useState<Catalog | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<string[]>("/api/catalog/categories").then(setCategories).catch((err: Error) => setError(err.message));
  }, []);

  useEffect(() => {
    const params = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
    if (q.trim()) params.set("q", q.trim());
    if (brand.trim()) params.set("brand", brand.trim());
    if (category) params.set("category", category);
    if (status) params.set("status", status);
    api<Catalog>(`/api/catalog?${params}`).then(setData).catch((err: Error) => setError(err.message));
  }, [q, brand, category, status, page]);

  const total = data ? Object.values(data.stats.by_status).reduce((sum, n) => sum + n, 0) : 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  useEffect(() => {
    if (data && page > pages) setPage(pages);
  }, [data, page, pages]);
  const filtering = Boolean(q || brand || category || status);

  return (
    <>
      <PageHead
        title="Products"
        caption="Every brand's catalog — the products the chat can quote, with the numbers the code-check uses."
      />
      {error && <div className="error">{error}</div>}
      <div className="filters">
        <label className="field grow">Search
          <input value={q} placeholder="SKU, English or native name" onChange={(event) => { setQ(event.target.value); setPage(1); }} />
        </label>
        <label className="field">Brand
          <input value={brand} placeholder="Brand name" onChange={(event) => { setBrand(event.target.value); setPage(1); }} />
        </label>
        <label className="field">Category
          <select value={category} onChange={(event) => { setCategory(event.target.value); setPage(1); }}>
            <option value="">All</option>
            {categories.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
        </label>
        <label className="field">Status
          <select value={status} onChange={(event) => { setStatus(event.target.value); setPage(1); }}>
            <option value="">All</option>
            {(meta?.product_statuses ?? []).map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
        </label>
      </div>
      {data && total === 0 && (
        <div className="empty">
          {filtering ? "No products match." : "The catalog is empty — load it with `dev catalog`."}
        </div>
      )}
      {data && total > 0 && (
        <>
          <Metrics items={[
            { label: "Products", value: total.toLocaleString("en-US") },
            { label: "Brands", value: data.stats.brands.toLocaleString("en-US") },
            { label: "Categories", value: String(data.stats.categories) },
            ...(meta?.product_statuses ?? []).map((item) => ({
              label: item,
              value: (data.stats.by_status[item] ?? 0).toLocaleString("en-US"),
            })),
          ]} />
          <DataTable
            rows={data.items}
            columns={[
              { key: "sku", label: "SKU" },
              { key: "product_name_en", label: "English name" },
              { key: "product_name_native", label: "Native name" },
              { key: "brand_name", label: "Brand" },
              { key: "product_status", label: "Status" },
              { key: "category", label: "Category" },
              { key: "subcategory", label: "Subcategory" },
              { key: "unit_size", label: "Unit size" },
              { key: "variant", label: "Variant" },
              { key: "form", label: "Form" },
              { key: "case_pack", label: "Case pack" },
              { key: "case_price", label: "Case price", render: (row) => money("USD", row.case_price) },
              { key: "moq_units", label: "MOQ (units)" },
              { key: "cases_per_layer", label: "Cases/layer" },
              { key: "layers_per_pallet", label: "Layers/pallet" },
              { key: "cases_per_pallet", label: "Cases/pallet" },
              { key: "case_weight", label: "Case kg", render: (row) => row.case_weight == null ? "—" : row.case_weight.toFixed(1) },
              { key: "policy_tags", label: "Policy tags" },
              { key: "restricted_territories", label: "Restricted in" },
              { key: "description_en", label: "Description", wrap: true },
            ]}
          />
          <div className="pager">
            <button className="btn" type="button" disabled={page <= 1} onClick={() => setPage((n) => n - 1)}>Previous</button>
            <span className="muted">
              {((page - 1) * PAGE_SIZE + 1).toLocaleString("en-US")}–{Math.min(page * PAGE_SIZE, total).toLocaleString("en-US")} of {total.toLocaleString("en-US")} · {pages.toLocaleString("en-US")} pages
            </span>
            <button className="btn" type="button" disabled={page >= pages} onClick={() => setPage((n) => n + 1)}>Next</button>
          </div>
        </>
      )}
    </>
  );
}
