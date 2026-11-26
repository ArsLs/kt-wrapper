import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Group, Stack, Text, Title } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { type CampaignSummary } from '../api/campaigns';
import { ApiError } from '../api/client';
import { canRetryWithSameKey, newIdempotencyKey } from '../api/idempotency';
import {
  fetchStreams,
  getStreams,
  previewStream,
  pushStream,
  setOfferPin,
  type SnapshotIn,
  type StreamSnapshot,
} from '../api/streams';
import {
  clearCampaignDrafts,
  clearDraft,
  draftBase,
  hasCampaignDrafts,
  readCampaignDrafts,
  toInput,
  writeDraft,
  type StoredDraft,
} from './drafts';
import { StreamCard } from './StreamCard';

const reason = (error: Error) =>
  error instanceof ApiError
    ? error.message
    : 'Network request failed. Please try again.';

const fail = (title: string, error: Error) =>
  notifications.show({ color: 'red', title, message: reason(error) });

export function CampaignEditor({
  campaignId,
  campaigns,
}: {
  campaignId: number;
  campaigns: ReturnType<typeof useQuery<CampaignSummary[]>>;
}) {
  const queryClient = useQueryClient();
  const campaign = campaigns.data?.find((item) => item.id === campaignId);
  const active = campaigns.isSuccess && campaign?.status === 'active';
  const streams = useQuery({
    queryKey: ['streams', campaignId],
    queryFn: () => getStreams(campaignId),
    enabled: active,
    refetchOnReconnect: false,
  });
  const [drafts, setDrafts] = useState(() => readCampaignDrafts(campaignId));
  const [restoredPushKeys] = useState(
    () => new Set(Object.values(drafts).map((draft) => draft.pushKey)),
  );
  const [verifiedFetch, setVerifiedFetch] = useState(false);
  const [busy, setBusy] = useState<Record<number, boolean>>({});
  const busyRef = useRef(new Set<number>());
  const fetchRef = useRef(false);
  const autoFetchStarted = useRef(false);
  const [initialSyncFinished, setInitialSyncFinished] = useState(false);
  const [fetching, setFetching] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const syncing = !initialSyncFinished || fetching;
  const { mutateAsync: fetchCampaignStreams } = useMutation({
    mutationFn: fetchStreams,
  });

  const syncStreams = useCallback(
    async (mode: 'auto' | 'manual') => {
      if (fetchRef.current || busyRef.current.size) return;
      fetchRef.current = true;
      setFetching(true);
      try {
        const key = ['streams', campaignId];
        await queryClient.cancelQueries({ queryKey: key });
        const result = await fetchCampaignStreams(campaignId);
        if (mode === 'manual') {
          clearCampaignDrafts(campaignId);
          setDrafts({});
        }
        queryClient.setQueryData(key, result);
        setVerifiedFetch(true);
        setFetchError(null);
      } catch (error) {
        if (
          error instanceof ApiError &&
          error.code === 'keitaro_invalid_offer_weights'
        ) {
          setFetchError(error.message);
        } else {
          fail('Fetch failed', error as Error);
        }
      } finally {
        fetchRef.current = false;
        setFetching(false);
      }
    },
    [campaignId, fetchCampaignStreams, queryClient],
  );

  useEffect(() => {
    if (
      !active ||
      !streams.isFetched ||
      streams.isFetching ||
      autoFetchStarted.current
    )
      return;
    autoFetchStarted.current = true;

    const legacyDrafts: Record<number, StoredDraft> = {};
    for (const [id, draft] of Object.entries(drafts)) {
      if (draft.base !== undefined) continue;
      const streamId = Number(id);
      const stream = streams.isSuccess
        ? streams.data.find((item) => item.id === streamId)
        : undefined;
      const updated = { ...draft, base: stream ? draftBase(stream) : null };
      writeDraft(campaignId, streamId, updated);
      legacyDrafts[streamId] = updated;
    }
    void syncStreams('auto').finally(() => {
      if (Object.keys(legacyDrafts).length)
        setDrafts((old) => ({ ...old, ...legacyDrafts }));
      setInitialSyncFinished(true);
    });
  }, [
    active,
    campaignId,
    drafts,
    streams.data,
    streams.isFetched,
    streams.isFetching,
    streams.isSuccess,
    syncStreams,
  ]);

  const incompatibleDrafts = new Map<number, string>();
  for (const [id, draft] of Object.entries(drafts)) {
    const streamId = Number(id);
    const stream = streams.data?.find((item) => item.id === streamId);
    if (
      stream?.status === 'published' &&
      stream.has_offers &&
      draft.base === draftBase(stream)
    )
      continue;
    incompatibleDrafts.set(streamId, stream?.name ?? `stream #${id}`);
  }

  const replace = (updated: StreamSnapshot) =>
    queryClient.setQueryData<StreamSnapshot[]>(['streams', campaignId], (old) =>
      old?.map((item) => (item.id === updated.id ? updated : item)),
    );
  function discardDraft(streamId: number) {
    clearDraft(campaignId, streamId);
    setDrafts((old) => {
      const next = { ...old };
      delete next[streamId];
      return next;
    });
  }
  async function runStream(
    streamId: number,
    action: () => Promise<void>,
    replayOnly = false,
  ) {
    if (
      syncing ||
      fetchRef.current ||
      busyRef.current.has(streamId) ||
      (!replayOnly && (incompatibleDrafts.has(streamId) || !streams.data))
    )
      return;
    busyRef.current.add(streamId);
    setBusy((old) => ({ ...old, [streamId]: true }));
    try {
      await action();
    } finally {
      busyRef.current.delete(streamId);
      setBusy((old) => ({ ...old, [streamId]: false }));
    }
  }
  async function preview(
    stream: StreamSnapshot,
    input: SnapshotIn,
  ): Promise<boolean> {
    let success = false;
    await runStream(stream.id, async () => {
      try {
        const result = await previewStream(stream.id, input);
        const previous = drafts[stream.id];
        const sameInput =
          previous &&
          JSON.stringify(
            toInput(previous.snapshot.offers, previous.snapshot.unpin_offer_ids),
          ) === JSON.stringify(toInput(result.offers, result.unpin_offer_ids));
        const draft = {
          snapshot: result,
          base: draftBase(stream),
          pushKey: sameInput ? previous.pushKey : undefined,
        };
        setDrafts((old) => ({ ...old, [stream.id]: draft }));
        if (!writeDraft(campaignId, stream.id, draft))
          notifications.show({
            color: 'yellow',
            title: 'Draft storage unavailable',
            message: 'This draft will be lost after reload.',
          });
        success = true;
      } catch (error) {
        fail('Preview failed', error as Error);
      }
    });
    return success;
  }
  function requiresReplay(streamId: number) {
    const key = drafts[streamId]?.pushKey;
    // The local snapshot can still predate a tracker write from the prior session.
    return (
      incompatibleDrafts.has(streamId) ||
      (key !== undefined && restoredPushKeys.has(key) && !verifiedFetch)
    );
  }
  async function push(streamId: number) {
    const draft = drafts[streamId];
    const replayOnly = requiresReplay(streamId);
    if (!draft || (replayOnly && !draft.pushKey)) return;
    await runStream(
      streamId,
      async () => {
        const pushKey = draft.pushKey ?? newIdempotencyKey();
        const attempted = { ...draft, pushKey };
        setDrafts((old) => ({ ...old, [streamId]: attempted }));
        if (!writeDraft(campaignId, streamId, attempted)) {
          notifications.show({
            color: 'yellow',
            title: 'Draft storage unavailable',
            message: 'The retry key could not be saved for reload.',
          });
        }

        const queryKey = ['streams', campaignId];
        let result: Awaited<ReturnType<typeof pushStream>>;
        try {
          await queryClient.cancelQueries({ queryKey });
          result = await pushStream(
            streamId,
            toInput(draft.snapshot.offers, draft.snapshot.unpin_offer_ids),
            pushKey,
            replayOnly,
          );
        } catch (error) {
          if (!canRetryWithSameKey(error) && !replayOnly) {
            const unsent = { ...draft, pushKey: undefined };
            writeDraft(campaignId, streamId, unsent);
            setDrafts((old) => ({ ...old, [streamId]: unsent }));
          }
          fail('Push failed', error as Error);
          return;
        }

        try {
          if (result.replayed) {
            const fresh = await getStreams(campaignId);
            const current = fresh.find((item) => item.id === streamId);
            queryClient.setQueryData<StreamSnapshot[]>(queryKey, (old) => {
              if (!old) return fresh;
              if (!current) return old.filter((item) => item.id !== streamId);
              if (old.some((item) => item.id === streamId))
                return old.map((item) => (item.id === streamId ? current : item));
              return [...old, current].sort(
                (a, b) => a.position - b.position || a.id - b.id,
              );
            });
          } else {
            replace(result.stream);
          }
          discardDraft(streamId);
        } catch (error) {
          fail('Push succeeded, but streams could not be refreshed', error as Error);
        }
      },
      replayOnly,
    );
  }
  async function pin(stream: StreamSnapshot, offerId: number, pinned: boolean) {
    if (drafts[stream.id]) return;
    await runStream(stream.id, async () => {
      try {
        await queryClient.cancelQueries({ queryKey: ['streams', campaignId] });
        replace(await setOfferPin(stream.id, offerId, pinned));
      } catch (error) {
        fail('Pin update failed', error as Error);
      }
    });
  }
  function fetchNow() {
    if (syncing || fetchRef.current || busyRef.current.size) return;
    if (
      (hasCampaignDrafts(campaignId) || Object.keys(drafts).length > 0) &&
      !window.confirm('Discard all drafts for this campaign and fetch streams?')
    )
      return;
    void syncStreams('manual');
  }
  function cancelDraft(streamId: number) {
    if (syncing || fetchRef.current || busyRef.current.has(streamId)) return;
    discardDraft(streamId);
  }
  return (
    <Stack>
      <Button
        component="a"
        href="/"
        variant="subtle"
        style={{ alignSelf: 'start' }}
      >
        Back to campaigns
      </Button>
      {campaigns.isLoading && <Text>Loading campaign...</Text>}
      {campaigns.isError && (
        <Alert color="red">
          Could not load campaign: {reason(campaigns.error)}.{' '}
          <Button variant="subtle" onClick={() => campaigns.refetch()}>
            Retry
          </Button>
        </Alert>
      )}
      {campaigns.isSuccess && !campaign && (
        <Alert color="red">Campaign not found.</Alert>
      )}
      {campaign?.status === 'removed' && (
        <Alert color="gray">
          Campaign “{campaign.name}” is Removed. Delete it from the campaign
          list.
        </Alert>
      )}
      {campaign?.status === 'active' && (
        <>
          <Group justify="space-between">
            <Title order={2}>{campaign.name} · Streams</Title>
            <Button
              onClick={fetchNow}
              loading={syncing}
              disabled={syncing || Object.values(busy).some(Boolean)}
            >
              Fetch streams
            </Button>
          </Group>
          {streams.isLoading && <Text>Loading streams...</Text>}
          {!streams.isLoading && syncing && (
            <Text>Syncing streams from Keitaro...</Text>
          )}
          {fetchError && (
            <Alert color="red" title="Fetch failed">
              {fetchError}
            </Alert>
          )}
          {initialSyncFinished && streams.isError && (
            <Alert color="red">
              Could not load streams: {reason(streams.error)}.{' '}
              <Button
                variant="subtle"
                disabled={syncing || streams.isFetching}
                onClick={() => streams.refetch()}
              >
                Retry
              </Button>
            </Alert>
          )}
          {!syncing && streams.isSuccess && !streams.data.length && (
            <Text>No streams yet.</Text>
          )}
          {initialSyncFinished &&
            Array.from(incompatibleDrafts, ([id, name]) => (
              <Alert key={id} color="yellow" title={`Draft for ${name}`}>
                <Group justify="space-between">
                  <Text size="sm">
                    The draft cannot be applied to the current published stream.
                  </Text>
                  <Group>
                    {drafts[id]?.pushKey && (
                      <Button
                        size="xs"
                        disabled={syncing || busy[id]}
                        onClick={() => push(id)}
                      >
                        Retry previous push
                      </Button>
                    )}
                    <Button
                      size="xs"
                      variant="light"
                      disabled={syncing || busy[id]}
                      onClick={() => cancelDraft(id)}
                    >
                      Cancel
                    </Button>
                  </Group>
                </Group>
              </Alert>
            ))}
          {initialSyncFinished &&
            streams.data?.map((stream) => {
              const incompatible = incompatibleDrafts.has(stream.id);
              return (
                <StreamCard
                  key={stream.id}
                  stream={stream}
                  draft={incompatible ? null : (drafts[stream.id]?.snapshot ?? null)}
                  busy={!!busy[stream.id] || syncing || incompatible}
                  replayOnly={requiresReplay(stream.id)}
                  onPreview={(input) => preview(stream, input)}
                  onPush={() => push(stream.id)}
                  onPin={(offerId, pinned) => pin(stream, offerId, pinned)}
                  onCancel={() => cancelDraft(stream.id)}
                />
              );
            })}
        </>
      )}
    </Stack>
  );
}
