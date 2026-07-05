/**
 * Titles, meta descriptions, deterministic answer-box prose, and JSON-LD
 * builders (SPEC §5). All prose is templated with slotted values — zero
 * LLM-generated filler, by design.
 */

export const SITE_NAME = process.env.SITE_NAME || 'RecallRepo';

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

export function monthYear(date: Date = new Date()): string {
  return `${MONTHS[date.getUTCMonth()]} ${date.getUTCFullYear()}`;
}

export function fmtInt(n: number): string {
  return n.toLocaleString('en-US');
}

export interface EntityBits {
  make?: string | null;
  model?: string | null;
  year?: number | null;
}

export function entityLabel({ make, model, year }: EntityBits): string {
  return [year, make, model].filter((x) => x != null && x !== '').join(' ');
}

// --- Titles & descriptions ---------------------------------------------------

export function yearPageTitle(bits: EntityBits, updated: Date = new Date()): string {
  return `${entityLabel(bits)} Recalls & Complaints (Updated ${monthYear(updated)}) | ${SITE_NAME}`;
}

export function hubPageTitle(label: string, updated: Date = new Date()): string {
  return `${label} Recalls & Complaints (Updated ${monthYear(updated)}) | ${SITE_NAME}`;
}

export function campaignPageTitle(campno: string, component: string | null): string {
  const comp = component ? ` — ${titleCaseComponent(component)}` : '';
  return `NHTSA Recall ${campno}${comp} | ${SITE_NAME}`;
}

export function titleCaseComponent(component: string): string {
  return component
    .split(':')[0]!
    .toLowerCase()
    .replace(/(^|[\s/])([a-z])/g, (m) => m.toUpperCase())
    .trim();
}

export function metaDescription(
  bits: EntityBits,
  recallCount: number,
  complaintTotal: number,
): string {
  const label = entityLabel(bits);
  if (recallCount === 0 && complaintTotal === 0) {
    return `NHTSA safety data for the ${label}: no recalls or complaints on record. Sourced from official NHTSA datasets, updated weekly.`;
  }
  return (
    `${label}: ${fmtInt(recallCount)} NHTSA recall${recallCount === 1 ? '' : 's'} and ` +
    `${fmtInt(complaintTotal)} owner complaint${complaintTotal === 1 ? '' : 's'}. ` +
    `Defects, consequences, and free repairs — from official NHTSA data, updated weekly.`
  );
}

// --- Answer box (SPEC §5, section 2) ----------------------------------------

export function answerBox(
  bits: EntityBits,
  recallCount: number,
  totalAffected: number,
  complaintTotal: number,
  topComponent: string | null,
  lastUpdated: string,
): string {
  const label = entityLabel(bits);
  if (recallCount === 0) {
    const complaints =
      complaintTotal > 0
        ? ` Owners have filed ${fmtInt(complaintTotal)} complaint${complaintTotal === 1 ? '' : 's'} with NHTSA${topComponent ? `, most often about the ${titleCaseComponent(topComponent).toLowerCase()}` : ''}.`
        : '';
    return (
      `No NHTSA recalls on record for the ${label}. That means NHTSA has no open safety ` +
      `campaign for this vehicle — it does not guarantee the absence of problems.${complaints} ` +
      `Last updated ${lastUpdated}.`
    );
  }
  const affected =
    totalAffected > 0 ? ` affecting an estimated ${fmtInt(totalAffected)} vehicles` : '';
  const complaints =
    complaintTotal > 0
      ? ` Owners have filed ${fmtInt(complaintTotal)} complaint${complaintTotal === 1 ? '' : 's'} with NHTSA${topComponent ? `, most often about the ${titleCaseComponent(topComponent).toLowerCase()}` : ''}.`
      : '';
  return (
    `The ${label} has ${recallCount} NHTSA recall campaign${recallCount === 1 ? '' : 's'}${affected}. ` +
    `Recall repairs are always free at any authorized dealer.${complaints} Last updated ${lastUpdated}.`
  );
}

// --- JSON-LD ------------------------------------------------------------------

export interface Crumb {
  name: string;
  path: string; // absolute path starting with /
}

export function breadcrumbJsonLd(siteUrl: string, crumbs: Crumb[]): object {
  return {
    '@context': 'https://schema.org',
    '@type': 'BreadcrumbList',
    itemListElement: crumbs.map((c, i) => ({
      '@type': 'ListItem',
      position: i + 1,
      name: c.name,
      item: `${siteUrl}${c.path}`,
    })),
  };
}

export interface Faq {
  q: string;
  a: string;
}

export function faqJsonLd(faqs: Faq[]): object {
  return {
    '@context': 'https://schema.org',
    '@type': 'FAQPage',
    mainEntity: faqs.map((f) => ({
      '@type': 'Question',
      name: f.q,
      acceptedAnswer: { '@type': 'Answer', text: f.a },
    })),
  };
}

export function yearPageFaqs(
  bits: EntityBits,
  recallCount: number,
  complaintTotal: number,
  topComponent: string | null,
): Faq[] {
  const label = entityLabel(bits);
  const faqs: Faq[] = [
    {
      q: `How many recalls does the ${label} have?`,
      a:
        recallCount === 0
          ? `The ${label} has no NHTSA safety recalls on record.`
          : `The ${label} has ${recallCount} NHTSA safety recall campaign${recallCount === 1 ? '' : 's'} on record.`,
    },
    {
      q: `How do I check if my ${label} has an open recall?`,
      a: `Enter your 17-character VIN at NHTSA's official recall lookup (nhtsa.gov/recalls) to see open, unrepaired recalls for your specific vehicle. Recall repairs are free.`,
    },
  ];
  if (complaintTotal > 0 && topComponent) {
    faqs.push({
      q: `What do owners complain about most on the ${label}?`,
      a: `Of ${fmtInt(complaintTotal)} NHTSA complaints on file, the most-reported component is ${titleCaseComponent(topComponent).toLowerCase()}.`,
    });
  }
  return faqs;
}

export function datasetJsonLd(siteUrl: string): object {
  return {
    '@context': 'https://schema.org',
    '@type': 'Dataset',
    name: 'NHTSA Vehicle Recalls, Complaints, and Investigations',
    description:
      'Public-domain vehicle safety data published by the U.S. National Highway Traffic Safety Administration, presented per vehicle entity.',
    url: `${siteUrl}/methodology/`,
    isBasedOn: 'https://www.nhtsa.gov/nhtsa-datasets-and-apis',
    license: 'https://www.usa.gov/government-works',
    creator: {
      '@type': 'GovernmentOrganization',
      name: 'National Highway Traffic Safety Administration',
    },
  };
}
