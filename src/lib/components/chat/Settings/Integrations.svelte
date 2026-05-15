<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { createEventDispatcher, onMount, getContext } from 'svelte';
	import { getToolServersData } from '$lib/apis';

	const dispatch = createEventDispatcher();
	const i18n = getContext('i18n');

	import { settings, toolServers, terminalServers } from '$lib/stores';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Plus from '$lib/components/icons/Plus.svelte';
	import Connection from './Tools/Connection.svelte';
	import Terminals from './Integrations/Terminals.svelte';

	import AddToolServerModal from '$lib/components/AddToolServerModal.svelte';

	import {
		getIntegrationStatus,
		connectMicrosoftPopup,
		disconnectMicrosoft,
		connectSlackPopup,
		disconnectSlack
	} from '$lib/apis/integrations';

	export let saveSettings: Function;

	let servers = null;
	let terminalServerConfigs: { url: string; key: string; name?: string; enabled: boolean }[] = [];
	let showConnectionModal = false;

	// ── Connected Apps ────────────────────────────────────────────────────────
	let integrationStatus: {
		microsoft: { enabled: boolean; connected: boolean; account?: { name: string; email: string } | null; expires_at?: number | null };
		slack: { enabled: boolean; connected: boolean; workspace?: string | null; expires_at?: number | null };
		work_iq: { enabled: boolean; connected: boolean; expires_at?: number | null };
	} | null = null;

	let microsoftConnecting = false;
	let slackConnecting = false;

	const loadIntegrationStatus = async () => {
		integrationStatus = await getIntegrationStatus(localStorage.token);
	};

	const handleMicrosoftConnect = async () => {
		microsoftConnecting = true;
		await connectMicrosoftPopup();
		await loadIntegrationStatus();
		microsoftConnecting = false;
		if (integrationStatus?.microsoft?.connected) {
			toast.success($i18n.t('Microsoft account connected'));
		}
	};

	const handleMicrosoftDisconnect = async () => {
		const res = await disconnectMicrosoft(localStorage.token);
		if (res) {
			await loadIntegrationStatus();
			toast.success($i18n.t('Microsoft account disconnected'));
		}
	};

	const handleSlackConnect = async () => {
		slackConnecting = true;
		await connectSlackPopup();
		await loadIntegrationStatus();
		slackConnecting = false;
		if (integrationStatus?.slack?.connected) {
			toast.success($i18n.t('Slack workspace connected'));
		}
	};

	const handleSlackDisconnect = async () => {
		const res = await disconnectSlack(localStorage.token);
		if (res) {
			await loadIntegrationStatus();
			toast.success($i18n.t('Slack workspace disconnected'));
		}
	};

	const addConnectionHandler = async (server) => {
		servers = [...servers, server];
		await updateHandler();
	};

	const updateHandler = async () => {
		await saveSettings({
			toolServers: servers,
			terminalServers: terminalServerConfigs
		});

		let toolServersData = await getToolServersData($settings?.toolServers ?? []);
		toolServersData = toolServersData.filter((data) => {
			if (data.error) {
				toast.error(
					$i18n.t(`Failed to connect to {{URL}} OpenAPI tool server`, { URL: data?.url })
				);
				return false;
			}
			return true;
		});
		toolServers.set(toolServersData);

		// Refresh terminal servers store (preserve system terminals)
		const existingSystemTerminals = ($terminalServers ?? []).filter((t) => t.id);
		const activeTerminals = terminalServerConfigs.filter((s) => s.enabled);
		if (activeTerminals.length > 0) {
			let terminalServersData = await getToolServersData(
				activeTerminals.map((t) => ({
					url: t.url,
					auth_type: t.auth_type ?? 'bearer',
					key: t.key ?? '',
					path: t.path ?? '/openapi.json',
					config: { enable: true }
				}))
			);
			terminalServersData = terminalServersData.filter((data) => data && !data.error);
			terminalServers.set([...terminalServersData, ...existingSystemTerminals]);
		} else {
			terminalServers.set(existingSystemTerminals);
		}
	};

	onMount(async () => {
		servers = $settings?.toolServers ?? [];
		terminalServerConfigs = $settings?.terminalServers ?? [];
		await loadIntegrationStatus();
	});
</script>

<AddToolServerModal bind:show={showConnectionModal} onSubmit={addConnectionHandler} direct />

<form
	id="tab-tools"
	class="flex flex-col h-full justify-between text-sm"
	on:submit|preventDefault={() => {
		updateHandler();
	}}
>
	<div class="overflow-y-scroll scrollbar-hidden h-full">
		{#if servers !== null}
			<div>
				<div class="pr-1.5">
					<div class="">
						<div class="flex justify-between items-center mb-0.5">
							<div class="font-medium">{$i18n.t('Manage Tool Servers')}</div>

							<Tooltip content={$i18n.t('Add Connection')}>
								<button
									aria-label={$i18n.t('Add Connection')}
									class="px-1"
									on:click={() => (showConnectionModal = true)}
									type="button"
								>
									<Plus />
								</button>
							</Tooltip>
						</div>

						<div class="flex flex-col gap-1.5">
							{#each servers as server, idx}
								<Connection
									bind:connection={server}
									direct
									onSubmit={() => updateHandler()}
									onDelete={() => {
										servers = servers.filter((_, i) => i !== idx);
										updateHandler();
									}}
								/>
							{/each}
						</div>
					</div>

					<div class="my-1.5">
						<div
							class={`text-xs ${($settings?.highContrastMode ?? false) ? 'text-gray-800 dark:text-gray-100' : 'text-gray-500'}`}
						>
							{$i18n.t('Connect to your own OpenAPI compatible external tool servers.')}
							<br />
							{$i18n.t(
								'CORS must be properly configured by the provider to allow requests from Open WebUI.'
							)}
						</div>
					</div>

					<div class="text-xs text-gray-600 dark:text-gray-300 mb-2">
						<a
							class="underline"
							href="https://github.com/open-webui/openapi-servers"
							target="_blank">{$i18n.t('Learn more about OpenAPI tool servers.')} ↗</a
						>
					</div>
				</div>

				<hr class="border-gray-100/50 dark:border-gray-850/50 my-4" />

				<div class="pr-1.5">
					<Terminals bind:servers={terminalServerConfigs} onChange={() => updateHandler()} />

					<div class="mt-1.5">
						<div
							class={`text-xs ${($settings?.highContrastMode ?? false) ? 'text-gray-800 dark:text-gray-100' : 'text-gray-500'}`}
						>
							{$i18n.t(
								'Connect to Open Terminal instances to browse files and use them as always-on tools. Only one can be active at a time.'
							)}
						</div>

						<div class="text-xs text-gray-600 dark:text-gray-300 mt-1">
							<a
								class="underline"
								href="https://github.com/open-webui/open-terminal"
								target="_blank">{$i18n.t('Learn more about Open Terminal')} ↗</a
							>
						</div>
					</div>
				</div>
			</div>

			<!-- Connected Apps Section -->
			{#if integrationStatus && (integrationStatus.microsoft?.enabled || integrationStatus.slack?.enabled || integrationStatus.work_iq?.enabled)}
				<hr class="border-gray-100/50 dark:border-gray-850/50 my-4" />

				<div class="pr-1.5">
					<div class="font-medium mb-3">{$i18n.t('Connected Apps')}</div>

					<div class="flex flex-col gap-3">
						<!-- Microsoft Card -->
						{#if integrationStatus.microsoft?.enabled}
							<div class="flex items-center justify-between p-3 rounded-xl border border-gray-200 dark:border-gray-700">
								<div class="flex items-center gap-3">
									<div class="size-9 flex items-center justify-center rounded-lg bg-blue-50 dark:bg-blue-950">
										<svg class="size-5" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
											<path d="M11.5 2L2 7v10l9.5 5 9.5-5V7L11.5 2z" fill="#5059C9"/>
											<path d="M13 7.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z" fill="#7B83EB"/>
											<path d="M21.5 9h-7A1.5 1.5 0 0 0 13 10.5v6.75A4.75 4.75 0 0 0 17.75 22 4.75 4.75 0 0 0 22.5 17.25V10.5A1.5 1.5 0 0 0 21.5 9z" fill="#7B83EB"/>
										</svg>
									</div>
									<div>
										<div class="text-sm font-medium">{$i18n.t('Microsoft')}</div>
										{#if integrationStatus.microsoft?.connected && integrationStatus.microsoft?.account}
											<div class="text-xs text-gray-500">{integrationStatus.microsoft.account.email}</div>
										{:else if integrationStatus.microsoft?.connected}
											<div class="text-xs text-green-500">{$i18n.t('Connected')}</div>
										{:else}
											<div class="text-xs text-gray-400">{$i18n.t('Teams, Outlook & Calendar')}</div>
										{/if}
										{#if integrationStatus.work_iq?.enabled}
											{#if integrationStatus.work_iq?.connected}
												<div class="text-xs text-blue-500 mt-0.5">{$i18n.t('Work IQ: connected')}</div>
											{:else if integrationStatus.microsoft?.connected}
												<div class="text-xs text-amber-500 mt-0.5">{$i18n.t('Work IQ: reconnect to activate')}</div>
											{/if}
										{/if}
									</div>
								</div>

								<div>
									{#if integrationStatus.microsoft?.connected}
										<button
											class="px-3 py-1 text-xs font-medium text-red-600 dark:text-red-400 border border-red-200 dark:border-red-800 rounded-full hover:bg-red-50 dark:hover:bg-red-950 transition"
											type="button"
											on:click={handleMicrosoftDisconnect}
										>
											{$i18n.t('Disconnect')}
										</button>
									{:else}
										<button
											class="px-3 py-1 text-xs font-medium text-blue-600 dark:text-blue-400 border border-blue-200 dark:border-blue-800 rounded-full hover:bg-blue-50 dark:hover:bg-blue-950 transition disabled:opacity-50"
											type="button"
											disabled={microsoftConnecting}
											on:click={handleMicrosoftConnect}
										>
											{microsoftConnecting ? $i18n.t('Connecting...') : $i18n.t('Connect')}
										</button>
									{/if}
								</div>
							</div>
						{/if}

						<!-- Slack Card -->
						{#if integrationStatus.slack?.enabled}
							<div class="flex items-center justify-between p-3 rounded-xl border border-gray-200 dark:border-gray-700">
								<div class="flex items-center gap-3">
									<div class="size-9 flex items-center justify-center rounded-lg bg-pink-50 dark:bg-pink-950">
										<svg class="size-4" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
											<path d="M5.042 15.165a2.528 2.528 0 0 1-2.52 2.523A2.528 2.528 0 0 1 0 15.165a2.527 2.527 0 0 1 2.522-2.52h2.52v2.52zM6.313 15.165a2.527 2.527 0 0 1 2.521-2.52 2.527 2.527 0 0 1 2.521 2.52v6.313A2.528 2.528 0 0 1 8.834 24a2.528 2.528 0 0 1-2.521-2.522v-6.313zM8.834 5.042a2.528 2.528 0 0 1-2.521-2.52A2.528 2.528 0 0 1 8.834 0a2.528 2.528 0 0 1 2.521 2.522v2.52H8.834zM8.834 6.313a2.528 2.528 0 0 1 2.521 2.521 2.528 2.528 0 0 1-2.521 2.521H2.522A2.528 2.528 0 0 1 0 8.834a2.528 2.528 0 0 1 2.522-2.521h6.312zM18.956 8.834a2.528 2.528 0 0 1 2.522-2.521A2.528 2.528 0 0 1 24 8.834a2.528 2.528 0 0 1-2.522 2.521h-2.522V8.834zM17.688 8.834a2.528 2.528 0 0 1-2.523 2.521 2.527 2.527 0 0 1-2.52-2.521V2.522A2.527 2.527 0 0 1 15.165 0a2.528 2.528 0 0 1 2.523 2.522v6.312zM15.165 18.956a2.528 2.528 0 0 1 2.523 2.522A2.528 2.528 0 0 1 15.165 24a2.527 2.527 0 0 1-2.52-2.522v-2.522h2.52zM15.165 17.688a2.527 2.527 0 0 1-2.52-2.523 2.526 2.526 0 0 1 2.52-2.52h6.313A2.527 2.527 0 0 1 24 15.165a2.528 2.528 0 0 1-2.522 2.523h-6.313z" fill="#E01E5A"/>
										</svg>
									</div>
									<div>
										<div class="text-sm font-medium">{$i18n.t('Slack')}</div>
										{#if integrationStatus.slack?.connected && integrationStatus.slack?.workspace}
											<div class="text-xs text-gray-500">{integrationStatus.slack.workspace}</div>
										{:else if integrationStatus.slack?.connected}
											<div class="text-xs text-green-500">{$i18n.t('Connected')}</div>
										{:else}
											<div class="text-xs text-gray-400">{$i18n.t('Messages & channels')}</div>
										{/if}
									</div>
								</div>

								<div>
									{#if integrationStatus.slack?.connected}
										<button
											class="px-3 py-1 text-xs font-medium text-red-600 dark:text-red-400 border border-red-200 dark:border-red-800 rounded-full hover:bg-red-50 dark:hover:bg-red-950 transition"
											type="button"
											on:click={handleSlackDisconnect}
										>
											{$i18n.t('Disconnect')}
										</button>
									{:else}
										<button
											class="px-3 py-1 text-xs font-medium text-purple-600 dark:text-purple-400 border border-purple-200 dark:border-purple-800 rounded-full hover:bg-purple-50 dark:hover:bg-purple-950 transition disabled:opacity-50"
											type="button"
											disabled={slackConnecting}
											on:click={handleSlackConnect}
										>
											{slackConnecting ? $i18n.t('Connecting...') : $i18n.t('Connect')}
										</button>
									{/if}
								</div>
							</div>
						{/if}
					</div>

					<div class="mt-2">
						<div class="text-xs text-gray-400">
							{$i18n.t('Connect your accounts once and use them across all chat sessions.')}
						</div>
					</div>
				</div>
			{/if}
		{:else}
			<div class="flex h-full justify-center">
				<div class="my-auto">
					<Spinner className="size-6" />
				</div>
			</div>
		{/if}
	</div>

	<div class="flex justify-end pt-3 text-sm font-medium">
		<button
			class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full"
			type="submit"
		>
			{$i18n.t('Save')}
		</button>
	</div>
</form>
