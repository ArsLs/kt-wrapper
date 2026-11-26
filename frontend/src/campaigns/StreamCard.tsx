import { useEffect, useState } from 'react';
import {
  ActionIcon,
  Badge,
  Button,
  Card,
  Combobox,
  Group,
  Table,
  Text,
  TextInput,
  Title,
  VisuallyHidden,
  useCombobox,
} from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { searchOffers } from '../api/offers';
import type {
  PreviewSnapshot,
  SnapshotIn,
  StreamSnapshot,
} from '../api/streams';
import { transition } from './drafts';

type Props = {
  stream: StreamSnapshot;
  draft: PreviewSnapshot | null;
  busy: boolean;
  replayOnly: boolean;
  onPreview: (input: SnapshotIn) => Promise<boolean>;
  onPush: () => void;
  onPin: (offerId: number, pinned: boolean) => void;
  onCancel: () => void;
};
export function StreamCard({
  stream,
  draft,
  busy,
  replayOnly,
  onPreview,
  onPush,
  onPin,
  onCancel,
}: Props) {
  const box = useCombobox();
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState('');
  const [query, setQuery] = useState('');
  const [debounced, setDebounced] = useState('');
  const [selected, setSelected] = useState<number | null>(null);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(query.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [query]);
  const search = useQuery({
    queryKey: ['offers', stream.id, debounced],
    queryFn: () => searchOffers(debounced, stream.id),
    enabled: open && !!debounced,
    refetchOnReconnect: false,
  });
  const offers = draft?.offers ?? stream.offers;
  const displayedOffers = [
    ...offers.filter((offer) => offer.status !== 'removed'),
    ...offers.filter((offer) => offer.status === 'removed'),
  ];
  const addable = (id: number, resultStatus: string | null) => {
    const current = offers.find((offer) => offer.offer_id === id);
    if (current) return current.status === 'removed';
    return resultStatus === null;
  };
  const selectedResult =
    selected === null || debounced !== query.trim()
      ? null
      : search.data?.find((item) => item.id === selected);
  if (stream.status === 'removed')
    return (
      <Card withBorder style={{ opacity: 0.55 }}>
        <Group>
          <Title order={4}>{stream.name}</Title>
          <Badge color="gray">Removed</Badge>
        </Group>
      </Card>
    );
  if (!stream.has_offers)
    return (
      <Card withBorder>
        <Title order={4}>{stream.name}</Title>
      </Card>
    );
  return (
    <Card withBorder bg={draft ? 'yellow.0' : undefined}>
      <Group justify="space-between">
        <Title order={4}>{stream.name}</Title>
        {draft && <Badge color="yellow">Unsaved changes</Badge>}
      </Group>
      <Table striped={!draft} mt="md">
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Name / ID</Table.Th>
            <Table.Th>Status</Table.Th>
            <Table.Th>Weight</Table.Th>
            <Table.Th w={1}>
              <VisuallyHidden>Manage offer</VisuallyHidden>
            </Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {displayedOffers.map((offer) => (
            <Table.Tr
              key={offer.offer_id}
              c={offer.status === 'removed' ? 'dimmed' : undefined}
            >
              <Table.Td>
                {offer.offer_name} (#{offer.offer_id})
              </Table.Td>
              <Table.Td>
                {offer.status === 'added'
                  ? 'Added'
                  : offer.status === 'removed'
                    ? 'Removed'
                    : 'Active'}
              </Table.Td>
              <Table.Td>
                <Group gap="xs" wrap="nowrap">
                  {offer.status === 'removed' ? 0 : offer.weight}%
                  {offer.status !== 'removed' && (
                    <ActionIcon
                      size="xs"
                      variant={offer.pinned ? 'filled' : 'light'}
                      aria-label={`Pin ${offer.offer_name}`}
                      aria-pressed={offer.pinned}
                      title={offer.pinned ? 'Unpin' : 'Pin'}
                      disabled={
                        busy ||
                        !!draft ||
                        (!offer.pinned && !offer.can_pin) ||
                        offer.status !== 'active'
                      }
                      onClick={() => onPin(offer.offer_id, !offer.pinned)}
                    >
                      <svg
                        width="16"
                        height="16"
                        viewBox="0 0 24 24"
                        fill={offer.pinned ? 'currentColor' : 'none'}
                        stroke="currentColor"
                        strokeWidth="2"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        aria-hidden="true"
                      >
                        <path
                          d="M8 3h8v2l-1 1v4l3 3v2H6v-2l3-3V6L8 5V3Z M12 15v6"
                          transform="rotate(40 12 12)"
                        />
                      </svg>
                    </ActionIcon>
                  )}
                </Group>
              </Table.Td>
              <Table.Td ta="right">
                {offer.status === 'removed' ? (
                  <Button
                    size="xs"
                    disabled={busy}
                    onClick={() =>
                      onPreview(
                        transition(stream, draft, offer.offer_id, 'restore'),
                      )
                    }
                  >
                    Bring back
                  </Button>
                ) : (
                  <ActionIcon
                    color="red"
                    variant="light"
                    aria-label={`Remove ${offer.offer_name}`}
                    title={`Remove ${offer.offer_name}`}
                    disabled={busy || !offer.can_remove}
                    onClick={() =>
                      onPreview(
                        transition(stream, draft, offer.offer_id, 'remove'),
                      )
                    }
                  >
                    <span aria-hidden="true">×</span>
                  </ActionIcon>
                )}
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      {!offers.length && <Text mt="sm">No offers in this stream.</Text>}
      <Group mt="md" align="end">
        <Combobox
          store={box}
          onOptionSubmit={(value) => {
            const item = search.data?.find(
              (offer) => String(offer.id) === value,
            );
            if (
              item &&
              debounced === query.trim() &&
              addable(item.id, item.status)
            ) {
              setSelected(item.id);
              setInput(`${item.name} (#${item.id})`);
            }
            box.closeDropdown();
          }}
        >
          <Combobox.Target>
            <TextInput
              label="Add offer"
              placeholder="Search by name or ID"
              value={input}
              disabled={busy}
              onFocus={() => {
                setOpen(true);
                box.openDropdown();
              }}
              onBlur={() => {
                setOpen(false);
                box.closeDropdown();
              }}
              onChange={(e) => {
                setInput(e.currentTarget.value);
                setQuery(e.currentTarget.value);
                setSelected(null);
                setOpen(true);
                box.openDropdown();
              }}
            />
          </Combobox.Target>
          <Combobox.Dropdown hidden={!query.trim()}>
            <Combobox.Options>
              {debounced === query.trim() &&
                search.data?.map((item) => (
                  <Combobox.Option
                    key={item.id}
                    value={String(item.id)}
                    disabled={!addable(item.id, item.status)}
                  >
                    {item.name} (#{item.id}){' '}
                    {offers.find((offer) => offer.offer_id === item.id)
                      ?.status ??
                      (item.status === null
                        ? ''
                        : `${item.status} · Fetch streams to sync`)}
                  </Combobox.Option>
                ))}
              {search.isFetching && (
                <Combobox.Empty>Searching...</Combobox.Empty>
              )}
              {search.isError && <Combobox.Empty>Search failed</Combobox.Empty>}
              {search.data?.length === 0 && !search.isFetching && (
                <Combobox.Empty>No active offers found</Combobox.Empty>
              )}
            </Combobox.Options>
          </Combobox.Dropdown>
        </Combobox>
        <Button
          disabled={
            busy ||
            !selectedResult ||
            !addable(selectedResult.id, selectedResult.status)
          }
          onClick={async () => {
            if (!selectedResult) return;
            const ok = await onPreview(
              transition(
                stream,
                draft,
                selectedResult.id,
                offers.some((offer) => offer.offer_id === selectedResult.id)
                  ? 'restore'
                  : 'add',
              ),
            );
            if (ok) {
              setInput('');
              setQuery('');
              setSelected(null);
              setOpen(false);
            }
          }}
        >
          Add
        </Button>
      </Group>
      {draft && (
        <Group mt="md">
          <Button disabled={busy} onClick={onPush}>
            {replayOnly ? 'Retry previous push' : 'Push to Keitaro'}
          </Button>
          <Button variant="light" disabled={busy} onClick={onCancel}>
            Cancel
          </Button>
        </Group>
      )}
    </Card>
  );
}
