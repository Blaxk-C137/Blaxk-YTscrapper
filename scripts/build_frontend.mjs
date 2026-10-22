#!/usr/bin/env node
/** Build the dependency-free CreatorScope frontend for Vercel. */

import { cp, mkdir, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';

const root = path.resolve(import.meta.dirname, '..');
const source = path.join(root, 'static');
const output = path.join(root, 'public');
const apiBase = String(process.env.CREATORSCOPE_API_URL || '').trim().replace(/\/$/, '');

if (!apiBase) {
  throw new Error('CREATORSCOPE_API_URL is required (for example, https://your-api.onrender.com)');
}
if (!/^https:\/\/[^/]+/i.test(apiBase)) {
  throw new Error('CREATORSCOPE_API_URL must be an HTTPS URL');
}

await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });
await cp(source, output, { recursive: true });
await writeFile(
  path.join(output, 'runtime-config.js'),
  `window.CREATOR_SCOPE_CONFIG = ${JSON.stringify({ apiBase })};\n`,
  'utf8',
);
console.log(`CreatorScope frontend built for ${apiBase}`);
