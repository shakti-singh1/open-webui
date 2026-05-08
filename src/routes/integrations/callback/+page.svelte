<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/stores';

	let status = 'processing';
	let provider = '';
	let message = '';

	onMount(() => {
		const params = new URLSearchParams(window.location.search);
		provider = params.get('provider') ?? '';
		status = params.get('status') ?? 'error';

		if (status === 'success') {
			message = `${provider ? provider.charAt(0).toUpperCase() + provider.slice(1) + ' ' : ''}Connected successfully! You can close this window.`;
		} else {
			message = 'Connection failed. You can close this window and try again.';
		}

		// Notify the opener and close the popup
		if (window.opener && !window.opener.closed) {
			try {
				window.opener.postMessage({ type: 'integration_callback', provider, status }, window.location.origin);
			} catch (_) {
				// cross-origin guard
			}
		}

		// Auto-close after a brief delay so the user sees the message
		setTimeout(() => {
			window.close();
		}, 1500);
	});
</script>

<div class="min-h-screen flex items-center justify-center bg-white dark:bg-gray-900">
	<div class="text-center p-8 max-w-sm">
		{#if status === 'success'}
			<div class="text-green-500 text-5xl mb-4">✓</div>
		{:else}
			<div class="text-red-500 text-5xl mb-4">✗</div>
		{/if}
		<p class="text-gray-700 dark:text-gray-200 text-sm">{message}</p>
		<p class="text-gray-400 text-xs mt-3">This window will close automatically.</p>
	</div>
</div>
