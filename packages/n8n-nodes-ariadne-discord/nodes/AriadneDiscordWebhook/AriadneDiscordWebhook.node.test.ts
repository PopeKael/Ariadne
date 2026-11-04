import assert from 'node:assert/strict';
import test from 'node:test';
import type { IExecuteFunctions } from 'n8n-workflow';
import { AriadneDiscordWebhook, validateWebhookUrl } from './AriadneDiscordWebhook.node';

const secret = 'https://discord.com/api/webhooks/123456789012345678/abcdefghijklmnopqrstuvwxyz';
const payload = {
	flags: 32768,
	allowed_mentions: { parse: [] },
	components: [{ type: 10, content: '**Ariadne card**' }],
};

function context(options: {
	input?: Array<{ json: Record<string, unknown> }>;
	operation?: string;
	destination?: string;
	response?: Record<string, unknown>;
	requestError?: Error;
} = {}) {
	const calls: unknown[] = [];
	const ctx = {
		getInputData: () => options.input || [],
		getNodeParameter: (name: string, _index: number, fallback?: unknown) =>
			name === 'operation' ? options.operation || fallback || 'send' : name === 'destination' ? options.destination || '' : fallback,
		getCredentials: async (name: string) => {
			assert.equal(name, 'discordWebhookApi');
			return { webhookUri: secret };
		},
		getNode: () => ({ name: 'Ariadne Discord Webhook' }),
		helpers: {
			request: async (request: unknown) => {
				calls.push(request);
				if (options.requestError) throw options.requestError;
				return options.response || { statusCode: 200, body: { id: '999999999999999999', channel_id: '888888888888888888', timestamp: '2026-11-04T00:00:00Z' }, headers: {} };
			},
		},
	};
	return { ctx: ctx as unknown as IExecuteFunctions, calls };
}

test('validates the configured Discord webhook origin and URL path', () => {
	assert.equal(validateWebhookUrl(secret), secret);
	for (const value of [
		'https://evil.example/api/webhooks/123456789012345678/abcdefghijklmnopqrstuvwxyz',
		`${secret}?leak=1`,
		'https://discord.com@evil.example/api/webhooks/123456789012345678/abcdefghijklmnopqrstuvwxyz',
	]) assert.throws(() => validateWebhookUrl(value));
});

test('posts the existing Components V2 payload and returns message metadata without the secret URL', async () => {
	const state = { destination: 'thailand_news', payload };
	const { ctx, calls } = context({
		input: [{ json: { state_json: JSON.stringify(state), delivery_key: 'video:thailand_news:123', attempt: 1 } }],
		destination: 'thailand_news',
	});
	const [[output]] = await new AriadneDiscordWebhook().execute.call(ctx);
	const request = calls[0] as Record<string, unknown>;
	assert.equal(request.method, 'POST');
	assert.equal(request.url, `${secret}?wait=true&with_components=true`);
	assert.deepEqual(request.body, payload);
	assert.equal((output.json.body as Record<string, unknown>).id, '999999999999999999');
	assert.equal(output.json.statusCode, 200);
	assert.equal(JSON.stringify(output).includes(secret), false);
});

test('inspects a saved webhook safely without posting a message', async () => {
	const { ctx, calls } = context({ operation: 'inspect', response: { statusCode: 200, body: { id: '1', channel_id: '2', name: 'News', url: secret }, headers: {} } });
	const [[output]] = await new AriadneDiscordWebhook().execute.call(ctx);
	assert.equal((calls[0] as Record<string, unknown>).method, 'GET');
	assert.deepEqual(output.json.body, { id: '1', channel_id: '2', name: 'News' });
	assert.equal(JSON.stringify(output).includes(secret), false);
});

test('fails closed on a mismatched destination before making a request', async () => {
	const { ctx, calls } = context({
		input: [{ json: { state_json: JSON.stringify({ destination: 'ai_noise', payload }) } }],
		destination: 'thailand_news',
	});
	await assert.rejects(() => new AriadneDiscordWebhook().execute.call(ctx), /invalid for this destination/);
	assert.equal(calls.length, 0);
});

test('keeps secret-like values in Discord error bodies out of execution output', async () => {
	const state = { destination: 'thailand_news', payload };
	const { ctx } = context({
		input: [{ json: { state_json: JSON.stringify(state), delivery_key: 'video:thailand_news:123' } }],
		destination: 'thailand_news',
		response: { statusCode: 403, body: { code: 50001, message: `failed for ${secret}`, id: secret }, headers: {} },
	});
	const [[output]] = await new AriadneDiscordWebhook().execute.call(ctx);
	assert.equal(output.json.statusCode, 403);
	assert.deepEqual(output.json.body, {});
	assert.equal(JSON.stringify(output).includes(secret), false);
});

test('replaces transport errors with a secret-free uncertain-outcome error', async () => {
	const { ctx } = context({
		input: [{ json: { state_json: JSON.stringify({ destination: 'thailand_news', payload }) } }],
		destination: 'thailand_news',
		requestError: new Error(`request to ${secret} failed`),
	});
	await assert.rejects(() => new AriadneDiscordWebhook().execute.call(ctx), (error: Error) => {
		assert.match(error.message, /outcome may be uncertain/);
		assert.equal(error.message.includes(secret), false);
		return true;
	});
});
