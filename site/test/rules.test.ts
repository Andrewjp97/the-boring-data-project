/**
 * Firestore security-rules test (SPEC Phase 2 acceptance): firestore.rules
 * must deny ALL client access — unauthenticated and authenticated alike —
 * on every collection. Server-side service accounts (Cloud Run render path,
 * ETL push) bypass rules entirely, so nothing may be allowed here.
 *
 * Runs inside the Firestore emulator via `pnpm --filter site test:rules`
 * (firebase emulators:exec sets FIRESTORE_EMULATOR_HOST). Skipped in the
 * plain `pnpm test` run where no emulator is available.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import {
  assertFails,
  initializeTestEnvironment,
  type RulesTestEnvironment,
} from '@firebase/rules-unit-testing';
import { deleteDoc, doc, getDoc, setDoc } from 'firebase/firestore';

const emulatorHost = process.env.FIRESTORE_EMULATOR_HOST;

describe.skipIf(!emulatorHost)('firestore.rules — deny-all client access', () => {
  let env: RulesTestEnvironment;

  beforeAll(async () => {
    const [host, port] = emulatorHost!.split(':');
    env = await initializeTestEnvironment({
      projectId: 'demo-recall-lookup',
      firestore: {
        host,
        port: Number(port),
        rules: readFileSync(
          fileURLToPath(new URL('../../firestore.rules', import.meta.url)),
          'utf8',
        ),
      },
    });
    // Seed a doc with rules disabled so denied *reads* are proven against
    // data that actually exists (not vacuous 404s).
    await env.withSecurityRulesDisabled(async (ctx) => {
      await setDoc(doc(ctx.firestore(), 'pages/recalls__honda__cr-v__2016'), {
        slug: 'recalls/honda/cr-v/2016',
        kind: 'year',
      });
    });
  });

  afterAll(async () => {
    await env?.cleanup();
  });

  const collections = ['pages', 'campaignPages', 'meta'];

  it('denies unauthenticated reads and writes on every collection', async () => {
    const db = env.unauthenticatedContext().firestore();
    await assertFails(getDoc(doc(db, 'pages/recalls__honda__cr-v__2016')));
    for (const coll of collections) {
      await assertFails(getDoc(doc(db, `${coll}/any-doc`)));
      await assertFails(setDoc(doc(db, `${coll}/any-doc`), { evil: true }));
      await assertFails(deleteDoc(doc(db, `${coll}/any-doc`)));
    }
  });

  it('denies authenticated (signed-in) clients too', async () => {
    const db = env.authenticatedContext('some-user').firestore();
    await assertFails(getDoc(doc(db, 'pages/recalls__honda__cr-v__2016')));
    for (const coll of collections) {
      await assertFails(setDoc(doc(db, `${coll}/any-doc`), { evil: true }));
    }
  });
});
