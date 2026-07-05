/// <reference types="astro/client" />

interface ImportMetaEnv {
  readonly PUBLIC_GA_ID?: string;
  readonly PUBLIC_GA_DEBUG?: string;
  readonly PUBLIC_ADS_ENABLED?: string;
  readonly PUBLIC_ADSENSE_CLIENT?: string;
  readonly PUBLIC_AMAZON_TAG?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
