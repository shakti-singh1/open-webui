<script lang="ts">
	import { onMount, onDestroy, getContext, createEventDispatcher, tick } from 'svelte';
	import { fade } from 'svelte/transition';
	import { flyAndScale } from '$lib/utils/transitions';
	import * as FocusTrap from 'focus-trap';

	const i18n = getContext('i18n');
	const dispatch = createEventDispatcher();

	export let show = false;
	export let data: {
		mode: 'form' | 'url';
		message: string;
		requestedSchema?: Record<string, any>;
		url?: string;
		elicitationId?: string;
	} | null = null;

	let modalElement: HTMLElement | null = null;
	let mounted = false;
	let focusTrap: FocusTrap.FocusTrap | null = null;

	// Form field values keyed by property name
	let fieldValues: Record<string, any> = {};

	$: if (show && data) {
		initFields();
	}

	function initFields() {
		fieldValues = {};
		if (data?.mode === 'form' && data.requestedSchema?.properties) {
			for (const [key, prop] of Object.entries(data.requestedSchema.properties as Record<string, any>)) {
				if (prop.type === 'boolean') {
					fieldValues[key] = prop.default ?? false;
				} else if (prop.type === 'integer' || prop.type === 'number') {
					fieldValues[key] = prop.default ?? '';
				} else if (prop.enum) {
					fieldValues[key] = prop.default ?? prop.enum[0] ?? '';
				} else {
					fieldValues[key] = prop.default ?? '';
				}
			}
		}
	}

	function getFieldLabel(key: string, prop: Record<string, any>): string {
		return prop.title || key;
	}

	function getProperties(): [string, Record<string, any>][] {
		if (!data?.requestedSchema?.properties) return [];
		return Object.entries(data.requestedSchema.properties as Record<string, any>);
	}

	function isRequired(key: string): boolean {
		return (data?.requestedSchema?.required ?? []).includes(key);
	}

	const handleKeyDown = (event: KeyboardEvent) => {
		if (event.key === 'Escape') {
			cancelHandler();
		}
	};

	const acceptHandler = async () => {
		show = false;
		await tick();
		if (data?.mode === 'url') {
			dispatch('accept', null);
		} else {
			// Coerce numeric fields
			const content: Record<string, any> = {};
			if (data?.requestedSchema?.properties) {
				for (const [key, prop] of Object.entries(data.requestedSchema.properties as Record<string, any>)) {
					const val = fieldValues[key];
					if (prop.type === 'integer') {
						content[key] = val !== '' ? parseInt(val, 10) : null;
					} else if (prop.type === 'number') {
						content[key] = val !== '' ? parseFloat(val) : null;
					} else if (prop.type === 'boolean') {
						content[key] = Boolean(val);
					} else {
						content[key] = val;
					}
				}
			}
			dispatch('accept', content);
		}
	};

	const declineHandler = async () => {
		show = false;
		await tick();
		dispatch('decline');
	};

	const cancelHandler = async () => {
		show = false;
		await tick();
		dispatch('cancel');
	};

	onMount(() => {
		mounted = true;
	});

	$: if (mounted) {
		if (show && modalElement) {
			document.body.appendChild(modalElement);
			focusTrap = FocusTrap.createFocusTrap(modalElement, { allowOutsideClick: true });
			focusTrap.activate();
			window.addEventListener('keydown', handleKeyDown);
			document.body.style.overflow = 'hidden';
		} else if (modalElement) {
			if (focusTrap) focusTrap.deactivate();
			window.removeEventListener('keydown', handleKeyDown);
			if (modalElement.parentNode === document.body) {
				document.body.removeChild(modalElement);
			}
			document.body.style.overflow = 'unset';
		}
	}

	onDestroy(() => {
		show = false;
		window.removeEventListener('keydown', handleKeyDown);
		if (focusTrap) focusTrap.deactivate();
		if (modalElement && modalElement.parentNode === document.body) {
			document.body.removeChild(modalElement);
		}
	});
</script>

{#if show && data}
	<!-- svelte-ignore a11y-click-events-have-key-events -->
	<!-- svelte-ignore a11y-no-static-element-interactions -->
	<div
		bind:this={modalElement}
		class="fixed top-0 right-0 left-0 bottom-0 bg-black/60 w-full h-screen max-h-[100dvh] flex justify-center z-99999999 overflow-hidden overscroll-contain"
		in:fade={{ duration: 10 }}
		on:mousedown={cancelHandler}
	>
		<div
			class="m-auto max-w-full w-[32rem] mx-2 bg-white/95 dark:bg-gray-950/95 backdrop-blur-sm rounded-4xl max-h-[90dvh] overflow-y-auto shadow-3xl border border-white dark:border-gray-900"
			in:flyAndScale
			on:mousedown={(e) => e.stopPropagation()}
		>
			<div class="px-[1.75rem] py-6 flex flex-col gap-4">
				<!-- Header -->
				<div class="flex items-center gap-2">
					<svg
						xmlns="http://www.w3.org/2000/svg"
						viewBox="0 0 20 20"
						fill="currentColor"
						class="w-5 h-5 text-blue-500 shrink-0"
					>
						<path
							fill-rule="evenodd"
							d="M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0Zm-7-4a1 1 0 1 1-2 0 1 1 0 0 1 2 0ZM9 9a.75.75 0 0 0 0 1.5h.253a.25.25 0 0 1 .244.304l-.459 2.066A1.75 1.75 0 0 0 10.747 15H11a.75.75 0 0 0 0-1.5h-.253a.25.25 0 0 1-.244-.304l.459-2.066A1.75 1.75 0 0 0 9.253 9H9Z"
							clip-rule="evenodd"
						/>
					</svg>
					<span class="text-lg font-semibold dark:text-gray-100">
						{$i18n.t('Tool Input Required')}
					</span>
				</div>

				<!-- Message -->
				{#if data.message}
					<p class="text-sm text-gray-600 dark:text-gray-300">{data.message}</p>
				{/if}

				<!-- Form fields -->
				{#if data.mode === 'form'}
					<div class="flex flex-col gap-3">
						{#each getProperties() as [key, prop]}
							<div class="flex flex-col gap-1">
								<label
									for="elicit-{key}"
									class="text-xs font-medium text-gray-700 dark:text-gray-300"
								>
									{getFieldLabel(key, prop)}{#if isRequired(key)}<span class="text-red-500 ml-0.5">*</span>{/if}
								</label>

								{#if prop.description}
									<p class="text-xs text-gray-500 dark:text-gray-500">{prop.description}</p>
								{/if}

								{#if prop.type === 'boolean'}
									<label class="flex items-center gap-2 cursor-pointer">
										<input
											id="elicit-{key}"
											type="checkbox"
											bind:checked={fieldValues[key]}
											class="w-4 h-4 rounded accent-blue-500"
										/>
										<span class="text-sm dark:text-gray-300">{getFieldLabel(key, prop)}</span>
									</label>
								{:else if prop.enum}
									<select
										id="elicit-{key}"
										bind:value={fieldValues[key]}
										class="w-full rounded-lg px-3 py-2 text-sm bg-gray-50 dark:bg-gray-900 dark:text-gray-100 border border-gray-200 dark:border-gray-700 outline-none focus:ring-2 focus:ring-blue-500/40"
									>
										{#each prop.enum as option}
											<option value={option}>{option}</option>
										{/each}
									</select>
								{:else if prop.type === 'integer' || prop.type === 'number'}
									<input
										id="elicit-{key}"
										type="number"
										step={prop.type === 'integer' ? '1' : 'any'}
										min={prop.minimum ?? undefined}
										max={prop.maximum ?? undefined}
										bind:value={fieldValues[key]}
										placeholder={prop.description ?? ''}
										required={isRequired(key)}
										class="w-full rounded-lg px-3 py-2 text-sm bg-gray-50 dark:bg-gray-900 dark:text-gray-100 border border-gray-200 dark:border-gray-700 outline-none focus:ring-2 focus:ring-blue-500/40"
									/>
								{:else}
									<textarea
										id="elicit-{key}"
										bind:value={fieldValues[key]}
										placeholder={prop.description ?? ''}
										required={isRequired(key)}
										rows="2"
										class="w-full rounded-lg px-3 py-2 text-sm bg-gray-50 dark:bg-gray-900 dark:text-gray-100 border border-gray-200 dark:border-gray-700 outline-none resize-none focus:ring-2 focus:ring-blue-500/40"
									/>
								{/if}
							</div>
						{/each}
					</div>
				{:else if data.mode === 'url'}
					<!-- URL mode: open the link and confirm when done -->
					<div class="flex flex-col gap-2">
						<a
							href={data.url}
							target="_blank"
							rel="noopener noreferrer"
							class="inline-flex items-center gap-1.5 text-sm text-blue-500 hover:underline break-all"
						>
							<svg
								xmlns="http://www.w3.org/2000/svg"
								viewBox="0 0 16 16"
								fill="currentColor"
								class="w-4 h-4 shrink-0"
							>
								<path
									d="M6.22 8.72a.75.75 0 0 0 1.06 1.06l5.22-5.22v1.69a.75.75 0 0 0 1.5 0v-3.5a.75.75 0 0 0-.75-.75h-3.5a.75.75 0 0 0 0 1.5h1.69L6.22 8.72Z"
								/>
								<path
									d="M3.5 6.75c0-.69.56-1.25 1.25-1.25H7A.75.75 0 0 0 7 4H4.75A2.75 2.75 0 0 0 2 6.75v4.5A2.75 2.75 0 0 0 4.75 14h4.5A2.75 2.75 0 0 0 12 11.25V9a.75.75 0 0 0-1.5 0v2.25c0 .69-.56 1.25-1.25 1.25h-4.5c-.69 0-1.25-.56-1.25-1.25v-4.5Z"
								/>
							</svg>
							{data.url}
						</a>
						<p class="text-xs text-gray-500 dark:text-gray-500">
							{$i18n.t('Open the link above, complete any required steps, then click Confirm.')}
						</p>
					</div>
				{/if}

				<!-- Action buttons -->
				<div class="flex justify-between gap-2 mt-2">
					<button
						type="button"
						class="text-sm bg-gray-100 hover:bg-gray-200 text-gray-800 dark:bg-gray-850 dark:hover:bg-gray-800 dark:text-white font-medium flex-1 py-2 rounded-3xl transition"
						on:click={declineHandler}
					>
						{$i18n.t('Decline')}
					</button>
					<button
						type="button"
						class="text-sm bg-gray-100 hover:bg-gray-200 text-gray-800 dark:bg-gray-850 dark:hover:bg-gray-800 dark:text-white font-medium flex-1 py-2 rounded-3xl transition"
						on:click={cancelHandler}
					>
						{$i18n.t('Cancel')}
					</button>
					<button
						type="button"
						class="text-sm bg-gray-900 hover:bg-gray-850 text-gray-100 dark:bg-gray-100 dark:hover:bg-white dark:text-gray-800 font-medium flex-1 py-2 rounded-3xl transition"
						on:click={acceptHandler}
					>
						{data.mode === 'url' ? $i18n.t('Confirm') : $i18n.t('Submit')}
					</button>
				</div>
			</div>
		</div>
	</div>
{/if}
