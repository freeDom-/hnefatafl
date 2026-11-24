/** Repeatable render-size comparison; no server, timing or capacity claim. */
import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { writeFileSync, unlinkSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';

const root = fileURLToPath(new URL('../../', import.meta.url));
const frontend = resolve(root, 'frontend');
const suffix = randomUUID().replaceAll('-', '');
const baselineName = `workshop-dom-baseline-${suffix}`;
const testName = `workshop-dom-benchmark-${suffix}.test.tsx`;
const baselinePath = resolve(frontend, 'src', `${baselineName}.tsx`);
const testPath = resolve(frontend, 'src', testName);
const output = resolve(process.argv[2] || '/tmp/hnefatafl-workshop-dom.json');
const baseline = '2579a86b82ed1eca91f39e7d8064196597e873ed';
const created = [];
try {
  writeFileSync(
    baselinePath,
    execFileSync('git', ['show', `${baseline}:frontend/src/TrainingJobNavigator.tsx`], {
      cwd: root,
    }),
    { flag: 'wx' },
  );
  created.push(baselinePath);
  writeFileSync(
    testPath,
    `import {writeFileSync} from 'node:fs';
import {cleanup,render} from '@testing-library/react';
import {expect,it,vi} from 'vitest';
import Baseline from './${baselineName}';
import Candidate from './TrainingJobNavigator';
import type {Job} from './types';
const jobs:Job[]=Array.from({length:1000},(_,i)=>({id:String(i).padStart(5,'0'),name:'Job '+i,status:'completed',created_at:'2026-01-01T00:00:00Z'}));
it('compares bounded rendering with identical 1000-job summaries',()=>{
 const result:Record<string,unknown>={};
 for(const [name,Component] of [['baseline',Baseline],['candidate',Candidate]] as const){
  render(<Component jobs={jobs} models={[]} onSelect={vi.fn()}/>);
  result[name]={rows:document.querySelectorAll('.job-row').length,options:document.querySelectorAll('.mobile-job-selector option').length,descendants:document.querySelectorAll('.training-job-navigator *').length};
  cleanup();
 }
 writeFileSync(${JSON.stringify(output)},JSON.stringify(result,null,2)+'\\n');
 expect((result.baseline as {rows:number}).rows).toBe(1000);
 expect((result.candidate as {rows:number}).rows).toBe(20);
});`,
    { flag: 'wx' },
  );
  created.push(testPath);
  execFileSync(
    process.execPath,
    ['node_modules/vitest/vitest.mjs', 'run', '--maxWorkers=1', `src/${testName}`],
    {
      cwd: frontend,
      stdio: 'inherit',
    },
  );
  console.log(`DOM counts: ${output}; baseline ${baseline}. No timing or server-load measurement.`);
} finally {
  for (const path of created.reverse()) {
    try {
      unlinkSync(path);
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
    }
  }
}
