/**
 * Screen 6 -- Banking Simulator.
 *
 * A working UI over the simulated CBS. Routes here match the `ui_route` values
 * in the business-rule catalogue, which is what the Playwright and TinyFish
 * browser drivers navigate to when capturing evidence.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import {
  BadgeCheck,
  Banknote,
  FileCheck2,
  Landmark,
  Scale,
  ScrollText,
  UserPlus,
  Users,
} from "lucide-react";
import { useState, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";

import {
  Badge,
  Card,
  EmptyState,
  ErrorState,
  KeyValue,
  LoadingBlock,
  PageHeader,
  SectionLabel,
  SimulationNotice,
  Spinner,
} from "../components/ui";
import { api } from "../lib/api";
import type { CbsAccount, CbsCustomer } from "../lib/types";

type Section = "customers" | "kyc" | "accounts" | "transactions" | "maker-checker" | "audit";

const SECTIONS: { key: Section; label: string; icon: typeof Users }[] = [
  { key: "customers", label: "Customers", icon: Users },
  { key: "kyc", label: "KYC", icon: FileCheck2 },
  { key: "accounts", label: "Accounts", icon: Landmark },
  { key: "transactions", label: "Transactions", icon: Banknote },
  { key: "maker-checker", label: "Maker-checker", icon: Scale },
  { key: "audit", label: "Audit trail", icon: ScrollText },
];

export default function Simulator() {
  const { section } = useParams<{ section?: string }>();
  const navigate = useNavigate();
  const active = (SECTIONS.find((s) => s.key === section)?.key ?? "customers") as Section;

  const reference = useQuery({ queryKey: ["cbsReference"], queryFn: api.cbs.reference });

  return (
    <>
      <PageHeader
        icon={Landmark}
        title="Banking Simulator"
        subtitle="A simulated Indian core banking system. Compliance tests execute against these exact endpoints, which is what makes the generated scenarios runnable."
      />

      <SimulationNotice className="mb-5" />

      <div className="mb-5 flex flex-wrap gap-1.5">
        {SECTIONS.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            type="button"
            onClick={() => navigate(`/simulator/${key}`)}
            className={clsx(
              "inline-flex items-center gap-2 rounded-lg px-3.5 py-2 text-[12.5px] font-medium transition-colors",
              active === key
                ? "bg-accent/12 text-slate-100 ring-1 ring-inset ring-accent/25"
                : "border border-white/10 bg-white/[.03] text-slate-400 hover:text-slate-200",
            )}
          >
            <Icon size={14} />
            {label}
          </button>
        ))}
      </div>

      {active === "customers" && <CustomersSection />}
      {active === "kyc" && <KycSection ovdTypes={reference.data?.accepted_ovd_types ?? []} />}
      {active === "accounts" && (
        <AccountsSection restrictionCodes={reference.data?.restriction_codes ?? []} />
      )}
      {active === "transactions" && <TransactionsSection />}
      {active === "maker-checker" && <MakerCheckerSection />}
      {active === "audit" && <AuditSection />}

      {reference.data && (
        <Card title="Rule parameters in force" className="mt-5">
          <div className="grid gap-5 md:grid-cols-3">
            <div>
              <SectionLabel>KYC periodicity (years, by risk)</SectionLabel>
              <ul className="space-y-1 text-[12.5px]">
                {Object.entries(reference.data.kyc_periodicity_years).map(([risk, years]) => (
                  <li key={risk} className="flex justify-between text-slate-400">
                    <span>{risk}</span>
                    <span className="mono text-slate-300">{years}</span>
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <SectionLabel>Accepted officially valid documents</SectionLabel>
              <div className="flex flex-wrap gap-1.5">
                {reference.data.accepted_ovd_types.map((type) => (
                  <Badge key={type}>{type.replace(/_/g, " ")}</Badge>
                ))}
              </div>
            </div>
            <div>
              <SectionLabel>Product limits (INR)</SectionLabel>
              <div className="table-wrap">
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>Product</th>
                      <th className="text-right">Per txn</th>
                      <th className="text-right">Daily</th>
                      <th className="text-right">Min bal</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(reference.data.products).map(([code, limits]) => (
                      <tr key={code}>
                        <td className="mono">{code}</td>
                        <td className="mono text-right">{limits.per_txn_limit}</td>
                        <td className="mono text-right">{limits.daily_debit_limit}</td>
                        <td className="mono text-right">{limits.minimum_balance}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </Card>
      )}
    </>
  );
}

/* ------------------------------------------------------------- customers */
function CustomersSection() {
  const queryClient = useQueryClient();
  const [query, setQuery] = useState("");
  const [showForm, setShowForm] = useState(false);

  const customers = useQuery({
    queryKey: ["cbsCustomers", query],
    queryFn: () => api.cbs.customers({ q: query || undefined, limit: 100 }),
  });

  const create = useMutation({
    mutationFn: (body: Record<string, unknown>) => api.cbs.createCustomer(body),
    onSuccess: () => {
      setShowForm(false);
      queryClient.invalidateQueries({ queryKey: ["cbsCustomers"] });
      queryClient.invalidateQueries({ queryKey: ["cbsMakerChecker"] });
    },
  });

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const payload: Record<string, unknown> = Object.fromEntries(form.entries());
    payload.is_pep = form.get("is_pep") === "on";
    // Empty optional fields must be omitted, not sent as "".
    for (const [key, value] of Object.entries(payload)) {
      if (value === "") delete payload[key];
    }
    create.mutate(payload);
  };

  return (
    <div className="space-y-5">
      {create.isError && <ErrorState error={create.error} />}

      <Card
        title={`Customers${customers.data ? ` (${customers.data.total})` : ""}`}
        bodyClassName="p-0"
        actions={
          <div className="flex items-center gap-2">
            <input
              className="input w-52"
              placeholder="Name, UCIC, PAN or mobile"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            <button
              type="button"
              className="btn-primary text-[12px]"
              onClick={() => setShowForm(!showForm)}
            >
              <UserPlus size={14} />
              {showForm ? "Cancel" : "Create customer"}
            </button>
          </div>
        }
      >
        {showForm && (
          <form onSubmit={onSubmit} className="border-b border-white/[.06] p-5">
            <SectionLabel>New individual customer (CBS-001)</SectionLabel>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Field name="first_name" label="First name *" required />
              <Field name="last_name" label="Last name *" required />
              <Field name="date_of_birth" label="Date of birth *" type="date" required />
              <Field name="mobile" label="Mobile *" placeholder="9876543210" required />
              <Field name="pan" label="PAN" placeholder="ABCDE1234F" />
              <Field name="aadhaar_last4" label="Aadhaar last 4" maxLength={4} />
              <Field name="email" label="Email" type="email" />
              <Field name="pincode" label="Pincode" placeholder="400001" />
              <Field name="address_line1" label="Address" />
              <Field name="city" label="City" />
              <Field name="state" label="State" />
              <div>
                <label className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-slate-500">
                  Customer type
                </label>
                <select name="customer_type" className="input" defaultValue="INDIVIDUAL">
                  <option value="INDIVIDUAL">Individual</option>
                  <option value="SOLE_PROPRIETOR">Sole proprietor</option>
                  <option value="COMPANY">Company</option>
                  <option value="TRUST">Trust</option>
                </select>
              </div>
            </div>
            <label className="mt-3 flex items-center gap-2 text-[12.5px] text-slate-400">
              <input type="checkbox" name="is_pep" className="accent-accent" />
              Politically exposed person (triggers HIGH risk and enhanced due diligence)
            </label>
            <div className="mt-4 flex gap-2">
              <button type="submit" className="btn-primary" disabled={create.isPending}>
                {create.isPending ? <Spinner /> : <UserPlus size={15} />}
                Create (maker)
              </button>
              <p className="self-center text-[11.5px] text-slate-500">
                Creation requires checker approval before the customer becomes ACTIVE.
              </p>
            </div>
          </form>
        )}

        {customers.isLoading ? (
          <div className="p-5">
            <LoadingBlock label="Loading customers" rows={3} />
          </div>
        ) : customers.data?.items.length ? (
          <div className="table-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th>UCIC</th>
                  <th>Name</th>
                  <th>PAN</th>
                  <th>Mobile</th>
                  <th>Risk</th>
                  <th>KYC</th>
                  <th>Next KYC due</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {customers.data.items.map((customer) => (
                  <CustomerRow key={customer.id} customer={customer} />
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            icon={Users}
            title="No customers in the simulator"
            body="Create one above, or run a generated test — the execution agent provisions its own clearly-marked test customers."
          />
        )}
      </Card>
    </div>
  );
}

function CustomerRow({ customer }: { customer: CbsCustomer }) {
  return (
    <tr>
      <td className="mono">{customer.ucic}</td>
      <td className="font-medium text-slate-200">
        {customer.first_name} {customer.last_name}
        {customer.is_pep && (
          <Badge tone="warn" className="ml-2">
            PEP
          </Badge>
        )}
      </td>
      <td className="mono">{customer.pan ?? "—"}</td>
      <td className="mono">{customer.mobile ?? "—"}</td>
      <td>
        <Badge
          tone={
            customer.risk_category === "HIGH"
              ? "fail"
              : customer.risk_category === "MEDIUM"
                ? "warn"
                : "muted"
          }
        >
          {customer.risk_category}
        </Badge>
      </td>
      <td>
        <Badge
          tone={
            customer.kyc_status === "VERIFIED"
              ? "pass"
              : customer.kyc_status === "REJECTED"
                ? "fail"
                : "muted"
          }
        >
          {customer.kyc_status.replace(/_/g, " ")}
        </Badge>
      </td>
      <td className="mono">{customer.next_kyc_due_date ?? "—"}</td>
      <td>
        <Badge
          tone={
            customer.status === "ACTIVE"
              ? "pass"
              : customer.status === "REJECTED"
                ? "fail"
                : "warn"
          }
        >
          {customer.status.replace(/_/g, " ")}
        </Badge>
      </td>
    </tr>
  );
}

/* -------------------------------------------------------------------- KYC */
function KycSection({ ovdTypes }: { ovdTypes: string[] }) {
  const queryClient = useQueryClient();
  const [customerId, setCustomerId] = useState("");

  const customers = useQuery({
    queryKey: ["cbsCustomers", ""],
    queryFn: () => api.cbs.customers({ limit: 100 }),
  });

  const kyc = useQuery({
    queryKey: ["cbsKyc", customerId],
    queryFn: () => api.cbs.kyc(customerId),
    enabled: Boolean(customerId),
  });

  const verify = useMutation({
    mutationFn: (documents: Record<string, unknown>[]) =>
      api.cbs.verifyKyc(customerId, {
        kyc_type: "CDD",
        documents,
        verification_mode: "IN_PERSON",
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["cbsKyc", customerId] });
      queryClient.invalidateQueries({ queryKey: ["cbsCustomers"] });
    },
  });

  const periodic = useMutation({
    mutationFn: () =>
      api.cbs.updateKyc(customerId, {
        kyc_type: "PERIODIC_UPDATION",
        self_declaration: true,
        documents: [],
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["cbsKyc", customerId] }),
  });

  const classify = useMutation({
    mutationFn: () => api.cbs.classifyRisk(customerId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["cbsKyc", customerId] });
      queryClient.invalidateQueries({ queryKey: ["cbsCustomers"] });
    },
  });

  const [selectedDocs, setSelectedDocs] = useState<string[]>(["PASSPORT", "PAN"]);

  return (
    <div className="space-y-5">
      {(verify.isError || periodic.isError || classify.isError) && (
        <div className="space-y-3">
          {verify.isError && <ErrorState error={verify.error} />}
          {periodic.isError && <ErrorState error={periodic.error} />}
          {classify.isError && <ErrorState error={classify.error} />}
        </div>
      )}

      <Card title="Customer Due Diligence (CBS-004, CBS-005, CBS-006, CBS-015)">
        <div className="mb-4">
          <label className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-slate-500">
            Select a customer
          </label>
          <select
            className="input max-w-md"
            value={customerId}
            onChange={(event) => setCustomerId(event.target.value)}
          >
            <option value="">Choose…</option>
            {(customers.data?.items ?? []).map((customer) => (
              <option key={customer.id} value={customer.id}>
                {customer.ucic} — {customer.first_name} {customer.last_name} (
                {customer.kyc_status})
              </option>
            ))}
          </select>
        </div>

        {!customerId ? (
          <EmptyState
            icon={FileCheck2}
            title="Select a customer"
            body="KYC verification, periodic updation and risk classification all operate on one customer at a time."
          />
        ) : kyc.isLoading ? (
          <LoadingBlock label="Loading KYC" rows={3} />
        ) : kyc.data ? (
          <>
            <dl className="grid grid-cols-2 gap-4 md:grid-cols-5">
              <KeyValue label="KYC status">
                <Badge tone={kyc.data.kyc_status === "VERIFIED" ? "pass" : "muted"}>
                  {kyc.data.kyc_status.replace(/_/g, " ")}
                </Badge>
              </KeyValue>
              <KeyValue label="Risk category">{kyc.data.risk_category}</KeyValue>
              <KeyValue label="Periodicity">{kyc.data.periodicity_years} years</KeyValue>
              <KeyValue label="Next due">
                <span className="mono">{kyc.data.next_kyc_due_date ?? "—"}</span>
                {kyc.data.kyc_due_now && (
                  <Badge tone="fail" className="ml-2">
                    due now
                  </Badge>
                )}
              </KeyValue>
              <KeyValue label="Requires EDD">
                {kyc.data.requires_edd ? (
                  <Badge tone="warn">yes</Badge>
                ) : (
                  <Badge>no</Badge>
                )}
              </KeyValue>
            </dl>

            <div className="mt-5 rounded-lg border border-white/[.07] bg-navy-950/40 p-4">
              <SectionLabel>Submit officially valid documents</SectionLabel>
              <div className="flex flex-wrap gap-2">
                {ovdTypes.map((type) => (
                  <label
                    key={type}
                    className={clsx(
                      "cursor-pointer rounded-md border px-2.5 py-1.5 text-[11.5px] transition-colors",
                      selectedDocs.includes(type)
                        ? "border-accent/40 bg-accent/10 text-accent-soft"
                        : "border-white/10 bg-white/[.03] text-slate-400",
                    )}
                  >
                    <input
                      type="checkbox"
                      className="sr-only"
                      checked={selectedDocs.includes(type)}
                      onChange={() =>
                        setSelectedDocs((previous) =>
                          previous.includes(type)
                            ? previous.filter((t) => t !== type)
                            : [...previous, type],
                        )
                      }
                    />
                    {type.replace(/_/g, " ")}
                  </label>
                ))}
              </div>
              <p className="mt-2 text-[11.5px] text-slate-500">
                At least one verified document must also evidence address — PAN alone is
                rejected with <span className="mono">NO_ADDRESS_PROOF</span>.
              </p>
              <div className="mt-3 flex flex-wrap gap-2">
                <button
                  type="button"
                  className="btn-primary text-[12px]"
                  disabled={verify.isPending || selectedDocs.length === 0}
                  onClick={() =>
                    verify.mutate(
                      selectedDocs.map((type) => ({
                        doc_type: type,
                        number: `SIM-${type.slice(0, 4)}`,
                        expiry_date: "2033-01-01",
                        verified: true,
                      })),
                    )
                  }
                >
                  {verify.isPending ? <Spinner /> : <BadgeCheck size={14} />}
                  Verify KYC
                </button>
                <button
                  type="button"
                  className="btn-ghost text-[12px]"
                  onClick={() => periodic.mutate()}
                  disabled={periodic.isPending}
                >
                  {periodic.isPending ? <Spinner /> : <FileCheck2 size={14} />}
                  Periodic updation (self-declaration)
                </button>
                <button
                  type="button"
                  className="btn-ghost text-[12px]"
                  onClick={() => classify.mutate()}
                  disabled={classify.isPending}
                >
                  {classify.isPending ? <Spinner /> : <Scale size={14} />}
                  Re-classify risk
                </button>
              </div>
              {classify.data && (
                <div className="mt-3 rounded border border-accent/20 bg-accent/[.06] px-3 py-2 text-[12px] text-slate-300">
                  <p className="font-medium text-accent-soft">
                    Risk: {classify.data.risk_category} · periodicity{" "}
                    {classify.data.periodicity_years} years
                  </p>
                  <ul className="mt-1 list-inside list-disc text-slate-400">
                    {classify.data.reasons.map((reason, index) => (
                      <li key={index}>{reason}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>

            <div className="mt-5">
              <SectionLabel>KYC record history (never overwritten)</SectionLabel>
              {kyc.data.records.length ? (
                <div className="table-wrap">
                  <table className="tbl">
                    <thead>
                      <tr>
                        <th>Ver.</th>
                        <th>Type</th>
                        <th>Status</th>
                        <th>Verified docs</th>
                        <th>Mode</th>
                        <th>Due date</th>
                        <th>Remarks</th>
                      </tr>
                    </thead>
                    <tbody>
                      {kyc.data.records.map((record) => (
                        <tr key={record.id}>
                          <td className="mono">v{record.version}</td>
                          <td>{record.kyc_type.replace(/_/g, " ")}</td>
                          <td>
                            <Badge tone={record.status === "VERIFIED" ? "pass" : "fail"}>
                              {record.status}
                            </Badge>
                          </td>
                          <td className="mono text-center">{record.verified_document_count}</td>
                          <td className="text-[11.5px]">{record.verification_mode ?? "—"}</td>
                          <td className="mono">{record.due_date ?? "—"}</td>
                          <td className="max-w-xs text-[11.5px] text-slate-400">
                            {record.remarks ?? "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="text-[12.5px] text-slate-500">No KYC records yet.</p>
              )}
            </div>
          </>
        ) : null}
      </Card>
    </div>
  );
}

/* --------------------------------------------------------------- accounts */
function AccountsSection({ restrictionCodes }: { restrictionCodes: string[] }) {
  const queryClient = useQueryClient();
  const [customerId, setCustomerId] = useState("");

  const customers = useQuery({
    queryKey: ["cbsCustomers", ""],
    queryFn: () => api.cbs.customers({ limit: 100 }),
  });
  const accounts = useQuery({
    queryKey: ["cbsAccounts"],
    queryFn: () => api.cbs.accounts({ limit: 100 }),
  });

  const open = useMutation({
    mutationFn: (body: Record<string, unknown>) => api.cbs.openAccount(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["cbsAccounts"] });
      queryClient.invalidateQueries({ queryKey: ["cbsMakerChecker"] });
    },
  });

  const restrict = useMutation({
    mutationFn: ({ id, add, remove }: { id: string; add?: string[]; remove?: string[] }) =>
      api.cbs.applyRestrictions(id, { add, remove, reason: "Applied from the simulator UI" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["cbsAccounts"] }),
  });

  const activate = useMutation({
    mutationFn: (id: string) => api.cbs.activateAccount(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["cbsAccounts"] }),
  });

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    open.mutate({
      customer_id: customerId,
      account_type: form.get("account_type"),
      product_code: form.get("product_code"),
      initial_deposit: form.get("initial_deposit") || "0",
    });
  };

  return (
    <div className="space-y-5">
      {(open.isError || restrict.isError) && (
        <div className="space-y-3">
          {open.isError && <ErrorState error={open.error} />}
          {restrict.isError && <ErrorState error={restrict.error} />}
        </div>
      )}

      <Card title="Open an account (CBS-007, CBS-008)">
        <form onSubmit={onSubmit} className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <div className="lg:col-span-2">
            <label className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-slate-500">
              Customer (must be ACTIVE with KYC VERIFIED)
            </label>
            <select
              className="input"
              value={customerId}
              onChange={(event) => setCustomerId(event.target.value)}
              required
            >
              <option value="">Choose…</option>
              {(customers.data?.items ?? []).map((customer) => (
                <option key={customer.id} value={customer.id}>
                  {customer.ucic} — {customer.first_name} {customer.last_name} (
                  {customer.status}/{customer.kyc_status})
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-slate-500">
              Account type
            </label>
            <select name="account_type" className="input" defaultValue="SAVINGS">
              <option value="SAVINGS">Savings</option>
              <option value="CURRENT">Current</option>
            </select>
          </div>
          <div>
            <label className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-slate-500">
              Product
            </label>
            <select name="product_code" className="input" defaultValue="SB-REG">
              <option value="SB-REG">SB-REG (regular savings)</option>
              <option value="SB-SMALL">SB-SMALL (small account)</option>
              <option value="SB-BSBDA">SB-BSBDA</option>
              <option value="CA-REG">CA-REG (current)</option>
            </select>
          </div>
          <Field name="initial_deposit" label="Initial deposit" placeholder="50000" />
          <div className="lg:col-span-5">
            <button
              type="submit"
              className="btn-primary"
              disabled={open.isPending || !customerId}
            >
              {open.isPending ? <Spinner /> : <Landmark size={15} />}
              Open account (maker)
            </button>
          </div>
        </form>
      </Card>

      <Card title={`Accounts${accounts.data ? ` (${accounts.data.total})` : ""}`} bodyClassName="p-0">
        {accounts.isLoading ? (
          <div className="p-5">
            <LoadingBlock label="Loading accounts" rows={3} />
          </div>
        ) : accounts.data?.items.length ? (
          <div className="table-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th>Account no.</th>
                  <th>Type</th>
                  <th>Product</th>
                  <th className="text-right">Balance</th>
                  <th className="text-right">Per txn</th>
                  <th className="text-right">Daily</th>
                  <th>Status</th>
                  <th>Restrictions</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {accounts.data.items.map((account) => (
                  <AccountRow
                    key={account.id}
                    account={account}
                    restrictionCodes={restrictionCodes}
                    onRestrict={(add, remove) =>
                      restrict.mutate({ id: account.id, add, remove })
                    }
                    onActivate={() => activate.mutate(account.id)}
                    busy={restrict.isPending || activate.isPending}
                  />
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            icon={Landmark}
            title="No accounts open"
            body="Open an account above. Account opening requires an ACTIVE customer whose KYC is VERIFIED and not lapsed."
          />
        )}
      </Card>
    </div>
  );
}

function AccountRow({
  account,
  restrictionCodes,
  onRestrict,
  onActivate,
  busy,
}: {
  account: CbsAccount;
  restrictionCodes: string[];
  onRestrict: (add?: string[], remove?: string[]) => void;
  onActivate: () => void;
  busy: boolean;
}) {
  const restrictions = account.restrictions ?? [];
  return (
    <tr>
      <td className="mono">{account.account_number}</td>
      <td>{account.account_type}</td>
      <td className="mono">{account.product_code}</td>
      <td className="mono text-right">{account.balance}</td>
      <td className="mono text-right text-slate-500">{account.per_txn_limit}</td>
      <td className="mono text-right text-slate-500">{account.daily_debit_limit}</td>
      <td>
        <Badge
          tone={
            account.status === "ACTIVE"
              ? "pass"
              : account.status === "CLOSED"
                ? "fail"
                : "warn"
          }
        >
          {account.status.replace(/_/g, " ")}
        </Badge>
      </td>
      <td>
        {restrictions.length ? (
          <div className="flex flex-wrap gap-1">
            {restrictions.map((code) => (
              <button
                key={code}
                type="button"
                onClick={() => onRestrict(undefined, [code])}
                disabled={busy}
                title="Lift this restriction"
                className="badge border-rose-500/30 bg-rose-500/10 text-rose-300 hover:bg-rose-500/20"
              >
                {code.replace(/_/g, " ")} ×
              </button>
            ))}
          </div>
        ) : (
          <span className="text-[11px] text-slate-600">none</span>
        )}
      </td>
      <td className="whitespace-nowrap">
        <div className="flex items-center gap-1.5">
          {account.status === "PENDING_APPROVAL" && (
            <button
              type="button"
              className="btn-ghost text-[11px]"
              onClick={onActivate}
              disabled={busy}
            >
              Activate
            </button>
          )}
          <select
            className="input w-32 py-1 text-[11px]"
            value=""
            onChange={(event) => event.target.value && onRestrict([event.target.value])}
            disabled={busy}
          >
            <option value="">Restrict…</option>
            {restrictionCodes
              .filter((code) => !restrictions.includes(code))
              .map((code) => (
                <option key={code} value={code}>
                  {code.replace(/_/g, " ")}
                </option>
              ))}
          </select>
        </div>
      </td>
    </tr>
  );
}

/* ----------------------------------------------------------- transactions */
function TransactionsSection() {
  const queryClient = useQueryClient();
  const accounts = useQuery({
    queryKey: ["cbsAccounts"],
    queryFn: () => api.cbs.accounts({ limit: 100 }),
  });
  const transactions = useQuery({
    queryKey: ["cbsTransactions"],
    queryFn: () => api.cbs.transactions({ limit: 100 }),
  });

  const create = useMutation({
    mutationFn: (body: Record<string, unknown>) => api.cbs.createTransaction(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["cbsTransactions"] });
      queryClient.invalidateQueries({ queryKey: ["cbsAccounts"] });
    },
  });

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    create.mutate(Object.fromEntries(form.entries()));
  };

  return (
    <div className="space-y-5">
      {create.isError && <ErrorState error={create.error} />}

      <Card title="Post a transaction (CBS-010, CBS-011, CBS-012)">
        <form onSubmit={onSubmit} className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <div className="lg:col-span-2">
            <label className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-slate-500">
              Account
            </label>
            <select name="account_id" className="input" required>
              <option value="">Choose…</option>
              {(accounts.data?.items ?? []).map((account) => (
                <option key={account.id} value={account.id}>
                  {account.account_number} — {account.product_code} — bal {account.balance} (
                  {account.status})
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-slate-500">
              Type
            </label>
            <select name="txn_type" className="input" defaultValue="DEBIT">
              <option value="DEBIT">Debit</option>
              <option value="CREDIT">Credit</option>
              <option value="TRANSFER">Transfer</option>
            </select>
          </div>
          <Field name="amount" label="Amount" placeholder="5000" required />
          <div>
            <label className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-slate-500">
              Channel
            </label>
            <select name="channel" className="input" defaultValue="BRANCH">
              {["BRANCH", "NEFT", "RTGS", "IMPS", "UPI", "CASH", "ATM"].map((channel) => (
                <option key={channel} value={channel}>
                  {channel}
                </option>
              ))}
            </select>
          </div>
          <div className="lg:col-span-4">
            <Field name="narration" label="Narration" />
          </div>
          <div className="self-end">
            <button type="submit" className="btn-primary w-full" disabled={create.isPending}>
              {create.isPending ? <Spinner /> : <Banknote size={15} />}
              Post
            </button>
          </div>
        </form>
        <p className="mt-3 text-[11.5px] text-slate-500">
          A rejected transaction is still persisted with its rejection code, because the
          rejection itself is auditable compliance evidence.
        </p>
      </Card>

      <Card
        title={`Transaction history${transactions.data ? ` (${transactions.data.total})` : ""}`}
        bodyClassName="p-0"
      >
        {transactions.isLoading ? (
          <div className="p-5">
            <LoadingBlock label="Loading transactions" rows={3} />
          </div>
        ) : transactions.data?.items.length ? (
          <div className="table-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th>Reference</th>
                  <th>Type</th>
                  <th>Channel</th>
                  <th className="text-right">Amount</th>
                  <th className="text-right">Fee</th>
                  <th className="text-right">Balance after</th>
                  <th>Status</th>
                  <th>Rejection</th>
                  <th>AML</th>
                </tr>
              </thead>
              <tbody>
                {transactions.data.items.map((txn) => (
                  <tr key={txn.id}>
                    <td className="mono text-[11px]">{txn.reference_number}</td>
                    <td>{txn.txn_type}</td>
                    <td className="text-[11.5px]">{txn.channel}</td>
                    <td className="mono text-right">{txn.amount}</td>
                    <td className="mono text-right text-slate-500">{txn.fee}</td>
                    <td className="mono text-right">{txn.balance_after ?? "—"}</td>
                    <td>
                      <Badge tone={txn.status === "POSTED" ? "pass" : "fail"}>{txn.status}</Badge>
                    </td>
                    <td className="max-w-[220px]">
                      {txn.rejection_code ? (
                        <>
                          <span className="mono text-[11px] text-rose-300">
                            {txn.rejection_code}
                          </span>
                          <p className="text-[10.5px] leading-snug text-slate-500">
                            {txn.rejection_reason}
                          </p>
                        </>
                      ) : (
                        <span className="text-slate-600">—</span>
                      )}
                    </td>
                    <td>
                      {txn.aml_flagged ? (
                        <Badge tone="warn" className="whitespace-normal">
                          flagged
                        </Badge>
                      ) : (
                        <span className="text-slate-600">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState icon={Banknote} title="No transactions yet" />
        )}
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------ maker-checker */
function MakerCheckerSection() {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState("PENDING");
  const [checkerId, setCheckerId] = useState("checker@sim");

  const tasks = useQuery({
    queryKey: ["cbsMakerChecker", status],
    queryFn: () => api.cbs.makerChecker(status),
  });

  const decide = useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: string }) =>
      api.cbs.decide(id, { decision, checker_id: checkerId, remarks: "Decided in the simulator" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["cbsMakerChecker"] });
      queryClient.invalidateQueries({ queryKey: ["cbsCustomers"] });
      queryClient.invalidateQueries({ queryKey: ["cbsAccounts"] });
    },
  });

  return (
    <div className="space-y-5">
      {decide.isError && <ErrorState error={decide.error} />}

      <Card
        title="Maker-checker queue (CBS-013)"
        bodyClassName="p-0"
        actions={
          <div className="flex items-center gap-2">
            <input
              className="input w-40"
              value={checkerId}
              onChange={(event) => setCheckerId(event.target.value)}
              placeholder="checker id"
              title="The checker must differ from the maker"
            />
            <select
              className="input w-36"
              value={status}
              onChange={(event) => setStatus(event.target.value)}
            >
              <option value="PENDING">Pending</option>
              <option value="APPROVED">Approved</option>
              <option value="REJECTED">Rejected</option>
            </select>
          </div>
        }
      >
        <div className="border-b border-white/[.06] px-5 py-3 text-[12px] text-slate-400">
          Dual control: the checker must be a different user from the maker. Approving as{" "}
          <span className="mono text-slate-300">maker@sim</span> is rejected with{" "}
          <span className="mono text-slate-300">SELF_APPROVAL_FORBIDDEN</span> — try it.
        </div>
        {tasks.isLoading ? (
          <div className="p-5">
            <LoadingBlock label="Loading tasks" rows={3} />
          </div>
        ) : tasks.data?.items.length ? (
          <div className="table-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th>Operation</th>
                  <th>Entity</th>
                  <th>Maker</th>
                  <th>Checker</th>
                  <th>Status</th>
                  <th>Created</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {tasks.data.items.map((task) => (
                  <tr key={task.id}>
                    <td className="font-medium text-slate-200">
                      {task.operation.replace(/_/g, " ")}
                    </td>
                    <td className="text-[11.5px]">
                      {task.entity_type}
                      {task.payload && (
                        <p className="mono text-[10.5px] text-slate-500">
                          {Object.entries(task.payload)
                            .map(([key, value]) => `${key}=${String(value)}`)
                            .join(" · ")}
                        </p>
                      )}
                    </td>
                    <td className="mono text-[11.5px]">{task.maker_id}</td>
                    <td className="mono text-[11.5px]">{task.checker_id ?? "—"}</td>
                    <td>
                      <Badge
                        tone={
                          task.status === "APPROVED"
                            ? "pass"
                            : task.status === "REJECTED"
                              ? "fail"
                              : "warn"
                        }
                      >
                        {task.status}
                      </Badge>
                    </td>
                    <td className="text-[11px]">
                      {new Date(task.created_at).toLocaleString()}
                    </td>
                    <td className="whitespace-nowrap">
                      {task.status === "PENDING" && (
                        <div className="flex gap-1.5">
                          <button
                            type="button"
                            className="btn-success text-[11px]"
                            onClick={() => decide.mutate({ id: task.id, decision: "APPROVE" })}
                            disabled={decide.isPending}
                          >
                            Approve
                          </button>
                          <button
                            type="button"
                            className="btn-danger text-[11px]"
                            onClick={() => decide.mutate({ id: task.id, decision: "REJECT" })}
                            disabled={decide.isPending}
                          >
                            Reject
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            icon={Scale}
            title={`No ${status.toLowerCase()} tasks`}
            body="Creating a customer or opening an account raises a task here for a second pair of eyes."
          />
        )}
      </Card>
    </div>
  );
}

/* ---------------------------------------------------------------- audit */
function AuditSection() {
  const [ruleCode, setRuleCode] = useState("");
  const audit = useQuery({
    queryKey: ["cbsAudit", ruleCode],
    queryFn: () => api.cbs.audit({ rule_code: ruleCode || undefined, limit: 200 }),
  });

  return (
    <Card
      title={`CBS audit trail${audit.data ? ` (${audit.data.total})` : ""}`}
      bodyClassName="p-0"
      actions={
        <input
          className="input w-40"
          placeholder="Filter by rule, e.g. CBS-004"
          value={ruleCode}
          onChange={(event) => setRuleCode(event.target.value.toUpperCase())}
        />
      }
    >
      <div className="border-b border-white/[.06] px-5 py-3 text-[12px] text-slate-400">
        Every operation writes an immutable event tagged with the CBS rule it exercised. This is
        what a compliance test cites as evidence that a control actually fired.
      </div>
      {audit.isLoading ? (
        <div className="p-5">
          <LoadingBlock label="Loading audit trail" rows={4} />
        </div>
      ) : audit.data?.items.length ? (
        <div className="table-wrap">
          <table className="tbl">
            <thead>
              <tr>
                <th>When</th>
                <th>Rule</th>
                <th>Entity</th>
                <th>Action</th>
                <th>Actor</th>
                <th>Change</th>
                <th>Message</th>
              </tr>
            </thead>
            <tbody>
              {audit.data.items.map((event) => (
                <tr key={event.id}>
                  <td className="whitespace-nowrap text-[11px]">
                    {new Date(event.created_at).toLocaleString()}
                  </td>
                  <td>
                    {event.rule_code ? (
                      <Badge tone="accent">{event.rule_code}</Badge>
                    ) : (
                      <span className="text-slate-600">—</span>
                    )}
                  </td>
                  <td className="text-[11.5px]">{event.entity_type}</td>
                  <td className="mono text-[11.5px]">{event.action}</td>
                  <td className="mono text-[11px]">{event.actor}</td>
                  <td className="max-w-[260px]">
                    {event.after_state ? (
                      <span className="mono text-[10.5px] text-slate-500">
                        {Object.entries(event.after_state)
                          .map(([key, value]) => `${key}=${String(value)}`)
                          .join(" · ")}
                      </span>
                    ) : (
                      <span className="text-slate-600">—</span>
                    )}
                  </td>
                  <td className="max-w-[240px] text-[11px] text-slate-400">
                    {event.message ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState icon={ScrollText} title="No audit events yet" />
      )}
    </Card>
  );
}

/* --------------------------------------------------------------- helpers */
function Field({
  name,
  label,
  type = "text",
  placeholder,
  required,
  maxLength,
}: {
  name: string;
  label: string;
  type?: string;
  placeholder?: string;
  required?: boolean;
  maxLength?: number;
}) {
  return (
    <div>
      <label
        htmlFor={`field-${name}`}
        className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-slate-500"
      >
        {label}
      </label>
      <input
        id={`field-${name}`}
        name={name}
        type={type}
        placeholder={placeholder}
        required={required}
        maxLength={maxLength}
        className="input"
      />
    </div>
  );
}
