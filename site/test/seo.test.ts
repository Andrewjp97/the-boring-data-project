import { describe, expect, it } from 'vitest';
import {
  answerBox,
  breadcrumbJsonLd,
  faqJsonLd,
  metaDescription,
  monthYear,
  titleCaseComponent,
  yearPageFaqs,
  yearPageTitle,
} from '../src/lib/seo';

const bits = { make: 'Honda', model: 'CR-V', year: 2016 };

describe('titles', () => {
  it('year page title carries updated month for CTR', () => {
    const title = yearPageTitle(bits, new Date(Date.UTC(2026, 6, 5)));
    expect(title).toBe('2016 Honda CR-V Recalls & Complaints (Updated July 2026) | RecallLookup');
  });

  it('monthYear is UTC-stable', () => {
    expect(monthYear(new Date(Date.UTC(2026, 0, 1)))).toBe('January 2026');
  });
});

describe('answer box (deterministic template, SPEC §5.1)', () => {
  it('recalls present', () => {
    const text = answerBox(bits, 4, 412000, 187, 'FUEL SYSTEM, GASOLINE', 'July 5, 2026');
    expect(text).toContain('The 2016 Honda CR-V has 4 NHTSA recall campaigns');
    expect(text).toContain('412,000 vehicles');
    expect(text).toContain('187 complaints');
    expect(text).toContain('Last updated July 5, 2026.');
  });

  it('zero recalls keeps the required empty-state phrasing', () => {
    const text = answerBox(
      { make: 'Mazda', model: 'CX-30', year: 2021 },
      0,
      0,
      12,
      'FORWARD COLLISION AVOIDANCE',
      'July 5, 2026',
    );
    expect(text).toContain('No NHTSA recalls on record for the 2021 Mazda CX-30');
    expect(text).toContain('12 complaints');
  });

  it('singular forms', () => {
    const text = answerBox(bits, 1, 100, 1, null, 'July 5, 2026');
    expect(text).toContain('1 NHTSA recall campaign affecting');
    expect(text).toContain('1 complaint with NHTSA');
  });
});

describe('meta description', () => {
  it('mentions both counts', () => {
    const d = metaDescription(bits, 4, 187);
    expect(d).toContain('4 NHTSA recalls');
    expect(d).toContain('187 owner complaints');
  });

  it('handles the empty page', () => {
    expect(metaDescription(bits, 0, 0)).toContain('no recalls or complaints on record');
  });
});

describe('JSON-LD builders', () => {
  it('breadcrumbs are positioned and absolute', () => {
    const ld = breadcrumbJsonLd('https://example.com', [
      { name: 'Home', path: '/' },
      { name: 'Honda', path: '/recalls/honda/' },
    ]) as any;
    expect(ld['@type']).toBe('BreadcrumbList');
    expect(ld.itemListElement[1]).toMatchObject({
      position: 2,
      name: 'Honda',
      item: 'https://example.com/recalls/honda/',
    });
  });

  it('faq page shape', () => {
    const ld = faqJsonLd([{ q: 'Q?', a: 'A.' }]) as any;
    expect(ld.mainEntity[0].acceptedAnswer.text).toBe('A.');
  });

  it('year page faqs include recall count and VIN guidance', () => {
    const faqs = yearPageFaqs(bits, 4, 187, 'FUEL SYSTEM, GASOLINE');
    expect(faqs).toHaveLength(3);
    expect(faqs[0]!.a).toContain('4 NHTSA safety recall campaigns');
    expect(faqs[1]!.a).toContain('nhtsa.gov/recalls');
    expect(faqs[2]!.a).toContain('fuel system');
  });
});

describe('titleCaseComponent', () => {
  it('takes the top-level component and title-cases it', () => {
    expect(titleCaseComponent('FUEL SYSTEM, GASOLINE:DELIVERY')).toBe('Fuel System, Gasoline');
    expect(titleCaseComponent('AIR BAGS')).toBe('Air Bags');
  });
});
