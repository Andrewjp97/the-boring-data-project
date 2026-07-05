/// <reference types="astro/client" />

interface ImportMetaEnv {
  readonly PUBLIC_GA_ID?: string;
  readonly PUBLIC_GA_DEBUG?: string;
  readonly PUBLIC_ADS_ENABLED?: string;
  readonly PUBLIC_ADSENSE_CLIENT?: string;
  readonly PUBLIC_ADSENSE_SLOT_YEAR_1?: string;
  readonly PUBLIC_ADSENSE_SLOT_YEAR_2?: string;
  readonly PUBLIC_ADSENSE_SLOT_MAKE?: string;
  readonly PUBLIC_ADSENSE_SLOT_MODEL?: string;
  readonly PUBLIC_ADSENSE_SLOT_CAMPAIGN?: string;
  readonly PUBLIC_AMAZON_TAG?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
