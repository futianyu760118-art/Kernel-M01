import test from 'node:test';
import assert from 'node:assert/strict';

import { buildOffboardSyncRequest, notifyEbmsOffboard } from '../src/m01-ebms-sync.js';

test('M01离职结果会生成可验签的EBMS同步请求', () => {
  const request = buildOffboardSyncRequest({
    url: 'http://ebms.test',
    secret: 'sync-secret',
    event: {
      event_id: 'EVT-292',
      person_id: 'P_EBMS_292',
      emp_code: '19',
      name: '宓曙',
      trace_id: 'TRACE-292'
    },
    timestamp: 1730545200000
  });

  assert.equal(request.url, 'http://ebms.test/api/m01/personnel/offboard');
  assert.equal(request.headers['X-M01-Timestamp'], '1730545200000');
  assert.match(request.headers['X-M01-Signature'], /^sha256=[a-f0-9]{64}$/);
  assert.equal(JSON.parse(request.body).person_id, 'P_EBMS_292');
});

test('EBMS不可用时同步结果明确返回失败', async () => {
  const result = await notifyEbmsOffboard({
    url: 'http://127.0.0.1:1',
    secret: 'sync-secret',
    event: { event_id: 'EVT-292', person_id: 'P_EBMS_292' },
    fetchImpl: fetch,
    timeoutMs: 100
  });
  assert.equal(result.ok, false);
  assert.equal(result.status, 'failed');
});
