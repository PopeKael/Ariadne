import type {
	IDataObject,
	IExecuteFunctions,
	INodeExecutionData,
	INodeType,
	INodeTypeDescription,
	IRequestOptions,
} from 'n8n-workflow';
import { NodeOperationError } from 'n8n-workflow';

const WEBHOOK_PATH = /^\/api\/webhooks\/\d{10,25}\/[A-Za-z0-9._-]{20,200}$/;
const SAFE_RESPONSE_FIELDS = [
	'id',
	'channel_id',
	'timestamp',
	'retry_after',
];
const SAFE_RATE_HEADERS = [
	'retry-after',
	'x-ratelimit-remaining',
	'x-ratelimit-reset-after',
	'x-ratelimit-reset',
];

export function validateWebhookUrl(value: unknown): string {
	if (typeof value !== 'string') throw new Error('invalid webhook URL');
	const raw = value.trim();
	let url: URL;
	try {
		url = new URL(raw);
	} catch {
		throw new Error('invalid webhook URL');
	}
	if (
		url.protocol !== 'https:' ||
		url.hostname !== 'discord.com' ||
		url.username ||
		url.password ||
		url.port ||
		url.search ||
		url.hash ||
		!WEBHOOK_PATH.test(url.pathname)
	) {
		throw new Error('invalid webhook URL');
	}
	return raw;
}

function safeObject(value: unknown, fields: string[], sensitiveValues: string[] = []): IDataObject {
	if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
	const input = value as IDataObject;
	const output: IDataObject = {};
	for (const field of fields) {
		const item = input[field];
		if (item === undefined) continue;
		if (typeof item === 'string' && sensitiveValues.some((sensitive) => sensitive && item.includes(sensitive))) continue;
		output[field] = item;
	}
	return output;
}

function parseState(value: unknown): IDataObject {
	if (typeof value !== 'string') throw new Error('missing delivery state');
	try {
		const parsed: unknown = JSON.parse(value);
		if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error();
		return parsed as IDataObject;
	} catch {
		throw new Error('invalid delivery state');
	}
}

function validatePayload(value: unknown): IDataObject {
	if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('invalid payload');
	const payload = value as IDataObject;
	const mentions = payload.allowed_mentions as IDataObject | undefined;
	if (
		payload.flags !== 32768 ||
		!mentions ||
		JSON.stringify(mentions) !== JSON.stringify({ parse: [] }) ||
		!Array.isArray(payload.components) ||
		payload.components.length === 0 ||
		['content', 'embeds', 'poll', 'files'].some((key) => key in payload)
	) {
		throw new Error('invalid Components V2 payload');
	}
	return payload;
}

export class AriadneDiscordWebhook implements INodeType {
	description: INodeTypeDescription = {
		displayName: 'Ariadne Discord Webhook',
		name: 'ariadneDiscordWebhook',
		group: ['output'],
		version: 1,
		description: 'Send Ariadne Components V2 cards through a saved Discord Webhook credential',
		defaults: { name: 'Ariadne Discord Webhook' },
		inputs: ['main'],
		outputs: ['main'],
		credentials: [{ name: 'discordWebhookApi', required: true }],
		properties: [
			{
				displayName: 'Operation',
				name: 'operation',
				type: 'options',
				options: [
					{ name: 'Send Card', value: 'send' },
					{ name: 'Inspect Webhook Destination', value: 'inspect' },
				],
				default: 'send',
				description: 'Inspection returns only webhook metadata; it never returns the webhook URL.',
			},
			{
				displayName: 'Configured Destination',
				name: 'destination',
				type: 'string',
				default: '',
				placeholder: 'thailand_news',
				description: 'Must match the publication candidate destination before sending.',
				displayOptions: { show: { operation: ['send'] } },
			},
		],
	};

	async execute(this: IExecuteFunctions): Promise<INodeExecutionData[][]> {
		const items = this.getInputData();
		const operation = this.getNodeParameter('operation', 0, 'send') as string;
		const expectedDestination = String(this.getNodeParameter('destination', 0, '') || '');
		let webhookUrl: string;
		try {
			const credential = await this.getCredentials('discordWebhookApi');
			webhookUrl = validateWebhookUrl(credential.webhookUri);
		} catch {
			throw new NodeOperationError(this.getNode(), 'The saved Discord webhook credential is unavailable or invalid.');
		}

		const output: INodeExecutionData[] = [];
		for (let index = 0; index < items.length || (operation === 'inspect' && index === 0); index++) {
			const item = items[index] || { json: {} };
			let url = webhookUrl;
			let body: IDataObject | undefined;
			let method: IRequestOptions['method'] = 'GET';

			if (operation === 'send') {
				let state: IDataObject;
				try {
					state = parseState(item.json.state_json);
					if (!expectedDestination || state.destination !== expectedDestination) throw new Error('destination mismatch');
					body = validatePayload(state.payload);
				} catch {
					throw new NodeOperationError(this.getNode(), 'The delivery candidate or Components V2 payload is invalid for this destination.');
				}
				method = 'POST';
				url += '?wait=true&with_components=true';
			} else if (operation !== 'inspect') {
				throw new NodeOperationError(this.getNode(), 'Unsupported Discord webhook operation.');
			}

			const request: IRequestOptions = {
				method,
				url,
				body,
				json: true,
				resolveWithFullResponse: true,
				simple: false,
				timeout: 20_000,
			};
			let response: IDataObject;
			try {
				response = (await this.helpers.request(request)) as IDataObject;
			} catch {
				throw new NodeOperationError(
					this.getNode(),
					'Discord webhook request failed; its delivery outcome may be uncertain. Check the delivery ledger before retrying.',
				);
			}

			const statusCode = Number(response.statusCode || response.status || 0);
			const webhookToken = new URL(webhookUrl).pathname.split('/').pop() || '';
			const sensitiveValues = [webhookUrl, webhookToken];
			const safeBody = safeObject(response.body, operation === 'inspect' ? ['id', 'channel_id', 'name', 'type'] : SAFE_RESPONSE_FIELDS, sensitiveValues);
			const safeHeaders = safeObject(response.headers, SAFE_RATE_HEADERS, sensitiveValues);
			if (operation === 'inspect' && statusCode >= 200 && statusCode < 300 && !safeBody.channel_id) {
				throw new NodeOperationError(this.getNode(), 'Discord did not return destination metadata for the saved webhook.');
			}

			output.push({
				json: operation === 'inspect'
					? { statusCode, body: safeBody, headers: safeHeaders }
					: { ...item.json, statusCode, body: safeBody, headers: safeHeaders },
				...(items[index] ? { pairedItem: { item: index } } : {}),
			});
			if (operation === 'inspect') break;
		}
		return [output];
	}
}
