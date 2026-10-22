#!/usr/bin/env node
/** Build the isolated CreatorScope frontend when Vercel Root Directory is static/. */

import { copyFile, mkdir, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const source = path.dirname(fileURLToPath(import.meta.url));
const output = path.join(source, 'public');
const apiBase = String(process.env.CREATORSCOPE_API_URL || '').trim().replace(/\/$/, '');

if (!apiBase) {
  throw new Error('CREATORSCOPE_API_URL is required (for example, https://your-api.onrender.com)');
}
if (!/^https:\/\/[^/]+/i.test(apiBase)) {
  throw new Error('CREATORSCOPE_API_URL must be an HTTPS URL');
}

await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });
for (const file of ['index.html', 'app.js', 'styles.css', 'favicon.svg']) {
  await copyFile(path.join(source, file), path.join(output, file));
}
await writeFile(
  path.join(output, 'runtime-config.js'),
  `window.CREATOR_SCOPE_CONFIG = ${JSON.stringify({ apiBase })};\n`,
  'utf8',
);
console.log(`CreatorScope frontend built for ${apiBase}`);
