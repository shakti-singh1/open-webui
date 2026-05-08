<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { createEventDispatcher, onMount, getContext, tick } from 'svelte';
	import { v4 as uuidv4 } from 'uuid';
	import { getModels as _getModels } from '$lib/apis';

	const dispatch = createEventDispatcher();
	const i18n = getContext('i18n');

	import { models, settings, user, terminalServers } from '$lib/stores';
	import { getTerminalServers } from '$lib/apis/terminal';
	import { WEBUI_API_BASE_URL } from '$lib/constants';

	import Switch from '$lib/components/common/Switch.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Plus from '$lib/components/icons/Plus.svelte';
	import Cog6 from '$lib/components/icons/Cog6.svelte';
	import Cloud from '$lib/components/icons/Cloud.svelte';
	import Connection from '$lib/components/chat/Settings/Tools/Connection.svelte';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';

	import AddToolServerModal from '$lib/components/AddToolServerModal.svelte';
	import AddTerminalServerModal from '$lib/components/AddTerminalServerModal.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';

	import {
		getToolServerConnections,
		setToolServerConnections,
		getTerminalServerConnections,
		setTerminalServerConnections
	} from '$lib/apis/configs';

	import { getTeamIntegrationsConfig, setTeamIntegrationsConfig } from '$lib/apis/integrations';

	export let saveSettings: Function;

	// ── Team Integrations ─────────────────────────────────────────────────────
	let microsoftEnabled = false;
	let microsoftClientId = '';
	let microsoftClientSecret = '';
	let microsoftTenantId = 'common';

	let slackEnabled = false;
	let slackClientId = '';
	let slackClientSecret = '';

	let teamIntegrationsSaving = false;

	const loadTeamIntegrationsConfig = async () => {
		const config = await getTeamIntegrationsConfig(localStorage.token);
		if (config) {
			microsoftEnabled = config.microsoft?.ENABLE_MICROSOFT_TEAMS_INTEGRATION ?? false;
			microsoftClientId = config.microsoft?.MICROSOFT_TEAMS_INTEGRATION_CLIENT_ID ?? '';
			microsoftClientSecret = config.microsoft?.MICROSOFT_TEAMS_INTEGRATION_CLIENT_SECRET ?? '';
			microsoftTenantId = config.microsoft?.MICROSOFT_TEAMS_INTEGRATION_TENANT_ID ?? 'common';

			slackEnabled = config.slack?.ENABLE_SLACK_INTEGRATION ?? false;
			slackClientId = config.slack?.SLACK_INTEGRATION_CLIENT_ID ?? '';
			slackClientSecret = config.slack?.SLACK_INTEGRATION_CLIENT_SECRET ?? '';
		}
	};

	const saveTeamIntegrationsConfig = async () => {
		teamIntegrationsSaving = true;
		const res = await setTeamIntegrationsConfig(localStorage.token, {
			microsoft: {
				ENABLE_MICROSOFT_TEAMS_INTEGRATION: microsoftEnabled,
				MICROSOFT_TEAMS_INTEGRATION_CLIENT_ID: microsoftClientId,
				MICROSOFT_TEAMS_INTEGRATION_CLIENT_SECRET: microsoftClientSecret,
				MICROSOFT_TEAMS_INTEGRATION_TENANT_ID: microsoftTenantId
			},
			slack: {
				ENABLE_SLACK_INTEGRATION: slackEnabled,
				SLACK_INTEGRATION_CLIENT_ID: slackClientId,
				SLACK_INTEGRATION_CLIENT_SECRET: slackClientSecret
			}
		});
		teamIntegrationsSaving = false;

		if (res) {
			toast.success($i18n.t('Team integrations saved successfully'));
		} else {
			toast.error($i18n.t('Failed to save team integrations'));
		}
	};

	let servers = null;
	let showConnectionModal = false;

	// Terminal server admin connections
	let terminalConnections = [];
	let showAddTerminalModal = false;
	let editTerminalIdx: number | null = null;
	let showDeleteTerminalConfirm = false;
	let deleteTerminalIdx: number | null = null;

	const addConnectionHandler = async (server) => {
		servers = [...servers, server];
		await updateHandler();
	};

	const updateHandler = async () => {
		const res = await setToolServerConnections(localStorage.token, {
			TOOL_SERVER_CONNECTIONS: servers
		}).catch((err) => {
			toast.error($i18n.t('Failed to save connections'));
			return null;
		});

		if (res) {
			toast.success($i18n.t('Connections saved successfully'));
		}
	};

	const saveTerminalServers = async () => {
		const res = await setTerminalServerConnections(localStorage.token, {
			TERMINAL_SERVER_CONNECTIONS: terminalConnections
		}).catch((err) => {
			toast.error($i18n.t('Failed to save terminal servers'));
			return null;
		});

		if (res) {
			toast.success($i18n.t('Terminal servers saved'));

			// Refresh the terminalServers store so changes are reflected immediately
			// Preserve user direct terminals, refresh system terminals from backend
			const existingDirectTerminals = ($terminalServers ?? []).filter((t) => !t.id);
			const systemTerminals = await getTerminalServers(localStorage.token);
			const systemEntries = systemTerminals.map((t) => ({
				id: t.id,
				url: `${WEBUI_API_BASE_URL}/terminals/${t.id}`,
				name: t.name,
				key: localStorage.token
			}));
			terminalServers.set([...existingDirectTerminals, ...systemEntries]);
		}
	};

	const addTerminalConnection = (server) => {
		terminalConnections = [...terminalConnections, { ...server, id: server.id ?? uuidv4() }];
		saveTerminalServers();
	};

	const updateTerminalConnection = (idx: number, updated) => {
		terminalConnections = terminalConnections.map((c, i) =>
			i === idx ? { ...c, ...updated, id: updated.id ?? c.id } : c
		);
		saveTerminalServers();
	};

	const removeTerminalConnection = (idx: number) => {
		terminalConnections = terminalConnections.filter((_, i) => i !== idx);
		saveTerminalServers();
	};

	onMount(async () => {
		const res = await getToolServerConnections(localStorage.token);
		servers = res.TOOL_SERVER_CONNECTIONS;

		// Load terminal server connections
		try {
			const terminalRes = await getTerminalServerConnections(localStorage.token);
			if (terminalRes?.TERMINAL_SERVER_CONNECTIONS) {
				terminalConnections = terminalRes.TERMINAL_SERVER_CONNECTIONS;
			}
		} catch {
			// Not configured yet
		}

		await loadTeamIntegrationsConfig();
	});
</script>

<AddToolServerModal bind:show={showConnectionModal} onSubmit={addConnectionHandler} />

<AddTerminalServerModal
	bind:show={showAddTerminalModal}
	edit={editTerminalIdx !== null}
	connection={editTerminalIdx !== null ? terminalConnections[editTerminalIdx] : null}
	onSubmit={(c) => {
		if (editTerminalIdx !== null) {
			updateTerminalConnection(editTerminalIdx, c);
			editTerminalIdx = null;
		} else {
			addTerminalConnection(c);
		}
	}}
	onDelete={() => {
		if (editTerminalIdx !== null) {
			deleteTerminalIdx = editTerminalIdx;
			showDeleteTerminalConfirm = true;
			editTerminalIdx = null;
		}
	}}
/>

<ConfirmDialog
	bind:show={showDeleteTerminalConfirm}
	on:confirm={() => {
		if (deleteTerminalIdx !== null) {
			removeTerminalConnection(deleteTerminalIdx);
			deleteTerminalIdx = null;
		}
	}}
/>

<form
	class="flex flex-col h-full justify-between text-sm"
	on:submit|preventDefault={() => {
		updateHandler();
	}}
>
	<div class=" overflow-y-scroll scrollbar-hidden h-full">
		{#if servers !== null}
			<div class="">
				<div class="mb-3">
					<div class=" mt-0.5 mb-2.5 text-base font-medium">{$i18n.t('General')}</div>

					<hr class=" border-gray-100/30 dark:border-gray-850/30 my-2" />

					<div class="mb-2.5 flex flex-col w-full justify-between">
						<div class="flex justify-between items-center mb-0.5">
							<div class="font-medium">{$i18n.t('Manage Tool Servers')}</div>

							<Tooltip content={$i18n.t(`Add Connection`)}>
								<button
									class="px-1"
									on:click={() => {
										showConnectionModal = true;
									}}
									type="button"
								>
									<Plus />
								</button>
							</Tooltip>
						</div>

						<div class="flex flex-col gap-1">
							{#each servers as server, idx}
								<Connection
									bind:connection={server}
									onSubmit={() => {
										updateHandler();
									}}
									onDelete={() => {
										servers = servers.filter((_, i) => i !== idx);
										updateHandler();
									}}
								/>
							{/each}
						</div>

						{#if servers.length === 0}
							<div class="text-xs text-gray-400 dark:text-gray-500">
								{$i18n.t('No tool server connections configured.')}
							</div>
						{/if}

						<div class="my-1.5">
							<div class="text-xs text-gray-500">
								{$i18n.t('Connect to your own OpenAPI compatible external tool servers.')}
							</div>
						</div>
					</div>

					<hr class=" border-gray-100/30 dark:border-gray-850/30 my-4" />

					<div class="mb-2.5 flex flex-col w-full">
						<div class="flex justify-between items-center mb-1">
							<div class="flex items-center gap-2">
								<div class="font-medium">{$i18n.t('Open Terminal')}</div>
								<span
									class="text-[0.65rem] font-medium uppercase px-1.5 py-0.5 rounded-full bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400"
									>{$i18n.t('Experimental')}</span
								>
							</div>

							<Tooltip content={$i18n.t('Add Connection')}>
								<button
									class="px-1"
									on:click={() => {
										editTerminalIdx = null;
										showAddTerminalModal = true;
									}}
									type="button"
								>
									<Plus />
								</button>
							</Tooltip>
						</div>

						<div class="flex flex-col gap-1.5">
							{#each terminalConnections as connection, idx}
								<div class="flex w-full gap-2 items-center">
									<Tooltip className="w-full relative" content={''} placement="top-start">
										<div class="flex w-full">
											<div
												class="flex-1 relative flex gap-1.5 items-center {connection?.enabled ===
												false
													? 'opacity-50'
													: ''}"
											>
												<Tooltip content={$i18n.t('Terminal')}>
													<Cloud className="size-4" strokeWidth="1.5" />
												</Tooltip>

												<div class="outline-hidden w-full bg-transparent text-sm">
													{connection.name || connection.url || $i18n.t('New Terminal')}
												</div>
											</div>
										</div>
									</Tooltip>

									<div class="flex gap-1 items-center">
										<Tooltip content={$i18n.t('Configure')}>
											<button
												class="self-center p-1 bg-transparent hover:bg-gray-100 dark:hover:bg-gray-850 rounded-lg transition"
												on:click={() => {
													editTerminalIdx = idx;
													showAddTerminalModal = true;
												}}
												type="button"
											>
												<Cog6 />
											</button>
										</Tooltip>

										<Tooltip
											content={connection?.enabled !== false
												? $i18n.t('Enabled')
												: $i18n.t('Disabled')}
										>
											<Switch
												state={connection?.enabled !== false}
												on:change={() => {
													terminalConnections = terminalConnections.map((c, i) =>
														i === idx ? { ...c, enabled: !(c?.enabled !== false) } : c
													);
													saveTerminalServers();
												}}
											/>
										</Tooltip>
									</div>
								</div>
							{/each}
						</div>

						{#if terminalConnections.length === 0}
							<div class="text-xs text-gray-400 dark:text-gray-500">
								{$i18n.t('No terminal connections configured.')}
							</div>
						{/if}

						<div class="mt-1.5">
							<div class="text-xs text-gray-500">
								{$i18n.t(
									'Connect to Open Terminal instances. All users will have access to file browsing and terminal tools through these servers.'
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
			</div>
		{:else}
			<div class="flex h-full justify-center">
				<div class="my-auto">
					<Spinner className="size-6" />
				</div>
			</div>
		{/if}

		<!-- Team Integrations Section -->
		<div class="mt-6">
			<div class="mt-0.5 mb-2.5 text-base font-medium">{$i18n.t('Team Integrations')}</div>
			<hr class="border-gray-100/30 dark:border-gray-850/30 my-2" />

			<!-- Microsoft Teams / Outlook / Calendar -->
			<div class="mb-5">
				<div class="flex items-center justify-between mb-3">
					<div class="flex items-center gap-2">
						<svg class="size-5" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
							<path d="M11.5 2L2 7v10l9.5 5 9.5-5V7L11.5 2z" fill="#5059C9"/>
							<path d="M13 7.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z" fill="#7B83EB"/>
							<path d="M21.5 9h-7A1.5 1.5 0 0 0 13 10.5v6.75A4.75 4.75 0 0 0 17.75 22 4.75 4.75 0 0 0 22.5 17.25V10.5A1.5 1.5 0 0 0 21.5 9z" fill="#7B83EB"/>
						</svg>
						<div class="font-medium">{$i18n.t('Microsoft Teams, Outlook & Calendar')}</div>
					</div>
					<Switch
						state={microsoftEnabled}
						on:change={() => (microsoftEnabled = !microsoftEnabled)}
					/>
				</div>

				{#if microsoftEnabled}
					<div class="flex flex-col gap-2 pl-2">
						<div>
							<div class="text-xs text-gray-500 mb-1">{$i18n.t('Azure App Client ID')}</div>
							<input
								class="w-full text-sm bg-transparent border border-gray-200 dark:border-gray-700 rounded-lg px-3 py-2 outline-none focus:border-blue-500"
								type="text"
								placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
								bind:value={microsoftClientId}
							/>
						</div>
						<div>
							<div class="text-xs text-gray-500 mb-1">{$i18n.t('Azure App Client Secret')}</div>
							<SensitiveInput
								placeholder={$i18n.t('Enter client secret')}
								bind:value={microsoftClientSecret}
							/>
						</div>
						<div>
							<div class="text-xs text-gray-500 mb-1">
								{$i18n.t('Tenant ID')}
								<span class="text-gray-400">({$i18n.t('use "common" for multi-tenant')})</span>
							</div>
							<input
								class="w-full text-sm bg-transparent border border-gray-200 dark:border-gray-700 rounded-lg px-3 py-2 outline-none focus:border-blue-500"
								type="text"
								placeholder="common"
								bind:value={microsoftTenantId}
							/>
						</div>
						<div class="text-xs text-gray-400 mt-1">
							{$i18n.t(
								'Register an Azure App with Mail.Read, Mail.Send, Calendars.ReadWrite, ChannelMessage.Send, and Chat.ReadWrite scopes. Set the redirect URI to: {url}',
								{ url: `${window.location.origin}/api/v1/integrations/microsoft/callback` }
							)}
						</div>
					</div>
				{/if}
			</div>

			<!-- Slack -->
			<div class="mb-3">
				<div class="flex items-center justify-between mb-3">
					<div class="flex items-center gap-2">
						<svg class="size-5" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
							<path d="M5.042 15.165a2.528 2.528 0 0 1-2.52 2.523A2.528 2.528 0 0 1 0 15.165a2.527 2.527 0 0 1 2.522-2.52h2.52v2.52zM6.313 15.165a2.527 2.527 0 0 1 2.521-2.52 2.527 2.527 0 0 1 2.521 2.52v6.313A2.528 2.528 0 0 1 8.834 24a2.528 2.528 0 0 1-2.521-2.522v-6.313zM8.834 5.042a2.528 2.528 0 0 1-2.521-2.52A2.528 2.528 0 0 1 8.834 0a2.528 2.528 0 0 1 2.521 2.522v2.52H8.834zM8.834 6.313a2.528 2.528 0 0 1 2.521 2.521 2.528 2.528 0 0 1-2.521 2.521H2.522A2.528 2.528 0 0 1 0 8.834a2.528 2.528 0 0 1 2.522-2.521h6.312zM18.956 8.834a2.528 2.528 0 0 1 2.522-2.521A2.528 2.528 0 0 1 24 8.834a2.528 2.528 0 0 1-2.522 2.521h-2.522V8.834zM17.688 8.834a2.528 2.528 0 0 1-2.523 2.521 2.527 2.527 0 0 1-2.52-2.521V2.522A2.527 2.527 0 0 1 15.165 0a2.528 2.528 0 0 1 2.523 2.522v6.312zM15.165 18.956a2.528 2.528 0 0 1 2.523 2.522A2.528 2.528 0 0 1 15.165 24a2.527 2.527 0 0 1-2.52-2.522v-2.522h2.52zM15.165 17.688a2.527 2.527 0 0 1-2.52-2.523 2.526 2.526 0 0 1 2.52-2.52h6.313A2.527 2.527 0 0 1 24 15.165a2.528 2.528 0 0 1-2.522 2.523h-6.313z" fill="#E01E5A"/>
						</svg>
						<div class="font-medium">{$i18n.t('Slack')}</div>
					</div>
					<Switch
						state={slackEnabled}
						on:change={() => (slackEnabled = !slackEnabled)}
					/>
				</div>

				{#if slackEnabled}
					<div class="flex flex-col gap-2 pl-2">
						<div>
							<div class="text-xs text-gray-500 mb-1">{$i18n.t('Slack App Client ID')}</div>
							<input
								class="w-full text-sm bg-transparent border border-gray-200 dark:border-gray-700 rounded-lg px-3 py-2 outline-none focus:border-blue-500"
								type="text"
								placeholder="xxxxxxxxxxxx.xxxxxxxxxxxx"
								bind:value={slackClientId}
							/>
						</div>
						<div>
							<div class="text-xs text-gray-500 mb-1">{$i18n.t('Slack App Client Secret')}</div>
							<SensitiveInput
								placeholder={$i18n.t('Enter client secret')}
								bind:value={slackClientSecret}
							/>
						</div>
						<div class="text-xs text-gray-400 mt-1">
							{$i18n.t(
								'Create a Slack App with channels:read, channels:history, chat:write, and search:read scopes. Set the redirect URI to: {url}',
								{ url: `${window.location.origin}/api/v1/integrations/slack/callback` }
							)}
						</div>
					</div>
				{/if}
			</div>

			<div class="flex justify-end mt-4">
				<button
					class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full disabled:opacity-50"
					type="button"
					disabled={teamIntegrationsSaving}
					on:click={saveTeamIntegrationsConfig}
				>
					{teamIntegrationsSaving ? $i18n.t('Saving...') : $i18n.t('Save Integrations')}
				</button>
			</div>
		</div>
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
