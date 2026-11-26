import { useEffect, useRef, useState } from 'react';
import {
  Alert,
  Badge,
  Button,
  Combobox,
  Group,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
  useCombobox,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError } from '../api/client';
import {
  createCampaign,
  deleteCampaign,
  type CampaignSummary,
} from '../api/campaigns';
import { canRetryWithSameKey, newIdempotencyKey } from '../api/idempotency';
import { getGeo } from '../api/geo';
import { searchOffers } from '../api/offers';
import {
  clearCreateAttempt,
  readCreateAttempt,
  saveCreateAttempt,
  type CreateAttempt,
} from './createAttempt';
import { clearCampaignDrafts } from './drafts';

const message = (error: Error) =>
  error instanceof ApiError
    ? error.message
    : 'Network request failed. Please try again.';

export function CampaignsPage({
  campaigns,
}: {
  campaigns: ReturnType<typeof useQuery<CampaignSummary[]>>;
}) {
  const queryClient = useQueryClient();
  const geo = useQuery({ queryKey: ['geo'], queryFn: getGeo });
  const box = useCombobox();
  const [attempt, setAttempt] = useState(readCreateAttempt);
  const createInProgress = useRef(false);
  const [name, setName] = useState(attempt?.name ?? '');
  const [selectedGeo, setSelectedGeo] = useState<string | null>(
    attempt?.geo ?? null,
  );
  const [offerInput, setOfferInput] = useState(attempt?.offerLabel ?? '');
  const [offerQuery, setOfferQuery] = useState('');
  const [debounced, setDebounced] = useState('');
  const [selectedOffer, setSelectedOffer] = useState<number | null>(
    attempt?.offer_id ?? null,
  );
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(offerQuery.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [offerQuery]);
  const offers = useQuery({
    queryKey: ['offers', debounced],
    queryFn: () => searchOffers(debounced),
    enabled: !!debounced,
  });
  const create = useMutation({
    mutationFn: ({ key, name, geo, offer_id }: CreateAttempt) =>
      createCampaign({ name, geo, offer_id }, key),
    onSuccess: (campaign, { key }) => {
      clearCreateAttempt(key);
      setAttempt(null);
      window.location.assign(`/campaigns/${campaign.id}`);
    },
    onError: (error: Error, { key }) => {
      if (!canRetryWithSameKey(error)) {
        clearCreateAttempt(key);
        setAttempt(null);
      }
      notifications.show({
        color: 'red',
        title: 'Create failed',
        message: message(error),
      });
    },
    onSettled: () => {
      createInProgress.current = false;
    },
  });
  function discardAttempt() {
    if (attempt) clearCreateAttempt(attempt.key);
    setAttempt(null);
    create.reset();
  }
  function submitCampaign() {
    if (
      createInProgress.current ||
      !selectedGeo ||
      !selectedOffer ||
      !name.trim()
    )
      return;
    const next = attempt ?? {
      key: newIdempotencyKey(),
      name: name.trim(),
      geo: selectedGeo,
      offer_id: selectedOffer,
      offerLabel: offerInput,
    };
    createInProgress.current = true;
    saveCreateAttempt(next);
    setAttempt(next);
    create.mutate(next);
  }
  const remove = useMutation({
    mutationFn: deleteCampaign,
    onSuccess: (_, id) => {
      clearCampaignDrafts(id);
      queryClient.setQueryData<CampaignSummary[]>(['campaigns'], (old) =>
        old?.filter((item) => item.id !== id),
      );
    },
    onError: (error: Error) =>
      notifications.show({
        color: 'red',
        title: 'Delete failed',
        message: message(error),
      }),
  });
  return (
    <Stack gap="xl">
      <section aria-label="New campaign">
        <Stack>
          <Title order={2}>New campaign</Title>
          <TextInput
            label="Name"
            value={name}
            disabled={create.isPending}
            onChange={(e) => {
              const value = e.currentTarget.value;
              if (value.trim() !== name.trim()) discardAttempt();
              setName(value);
            }}
            required
          />
          <Select
            label="Geo"
            searchable
            data={(geo.data ?? []).map((item) => ({
              value: item.code,
              label: `${item.name} (${item.code})`,
            }))}
            value={selectedGeo}
            disabled={create.isPending}
            onChange={(value) => {
              if (value !== selectedGeo) discardAttempt();
              setSelectedGeo(value);
            }}
            error={geo.isError ? 'Could not load countries' : undefined}
          />
          <Combobox
            store={box}
            onOptionSubmit={(value) => {
              const offer = offers.data?.find(
                (item) => String(item.id) === value,
              );
              if (offer && debounced === offerQuery.trim()) {
                if (selectedOffer !== offer.id) discardAttempt();
                setSelectedOffer(offer.id);
                setOfferInput(`${offer.name} (#${offer.id})`);
              }
              box.closeDropdown();
            }}
          >
            <Combobox.Target>
              <TextInput
                label="Offer"
                placeholder="Search by offer ID or name"
                value={offerInput}
                disabled={create.isPending}
                onFocus={() => box.openDropdown()}
                onBlur={() => box.closeDropdown()}
                onChange={(e) => {
                  const value = e.currentTarget.value;
                  if (value !== offerInput) discardAttempt();
                  setOfferInput(value);
                  setOfferQuery(value);
                  setSelectedOffer(null);
                  box.openDropdown();
                }}
                error={offers.isError ? 'Could not search offers' : undefined}
              />
            </Combobox.Target>
            <Combobox.Dropdown hidden={!offerQuery.trim()}>
              <Combobox.Options>
                {debounced === offerQuery.trim() &&
                  offers.data?.map((offer) => (
                    <Combobox.Option key={offer.id} value={String(offer.id)}>
                      {offer.name} (#{offer.id})
                    </Combobox.Option>
                  ))}
                {offers.isFetching && (
                  <Combobox.Empty>Searching...</Combobox.Empty>
                )}
                {!offers.isFetching && offers.data?.length === 0 && (
                  <Combobox.Empty>No active offers found</Combobox.Empty>
                )}
              </Combobox.Options>
            </Combobox.Dropdown>
          </Combobox>
          {create.isError && (
            <Alert color="red" title="Create failed">
              {message(create.error)}
            </Alert>
          )}
          <Group>
            <Button
              loading={create.isPending}
              disabled={!name.trim() || !selectedGeo || !selectedOffer}
              onClick={submitCampaign}
            >
              {attempt ? 'Retry create' : 'Create'}
            </Button>
            {attempt && (
              <Button
                variant="light"
                disabled={create.isPending}
                onClick={() => {
                  discardAttempt();
                  setName('');
                  setSelectedGeo(null);
                  setSelectedOffer(null);
                  setOfferInput('');
                  setOfferQuery('');
                }}
              >
                Reset
              </Button>
            )}
          </Group>
        </Stack>
      </section>
      <section aria-label="Campaigns">
        <Group justify="space-between">
          <Title order={2}>Campaigns</Title>
        </Group>
        {campaigns.isLoading && <Text>Loading campaigns...</Text>}
        {campaigns.isError && (
          <Alert color="red">
            Could not load campaigns: {message(campaigns.error)}.
            <Button variant="subtle" onClick={() => campaigns.refetch()}>
              Retry
            </Button>
          </Alert>
        )}
        {campaigns.isSuccess &&
          (campaigns.data.length ? (
            <Table striped>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Name</Table.Th>
                  <Table.Th>Geo</Table.Th>
                  <Table.Th>Status</Table.Th>
                  <Table.Th>Actions</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {campaigns.data.map((item) => (
                  <Table.Tr
                    key={item.id}
                    style={{ opacity: item.status === 'removed' ? 0.55 : 1 }}
                  >
                    <Table.Td>{item.name}</Table.Td>
                    <Table.Td>{item.geo}</Table.Td>
                    <Table.Td>
                      <Badge
                        color={item.status === 'removed' ? 'gray' : 'green'}
                      >
                        {item.status === 'removed' ? 'Removed' : 'Active'}
                      </Badge>
                    </Table.Td>
                    <Table.Td>
                      <Group>
                        {item.status === 'active' && (
                          <Button
                            component="a"
                            href={`/campaigns/${item.id}`}
                            size="xs"
                          >
                            Open
                          </Button>
                        )}
                        <Button
                          size="xs"
                          color="red"
                          variant="light"
                          disabled={remove.isPending}
                          onClick={() => {
                            if (
                              window.confirm(`Delete campaign “${item.name}”?`)
                            )
                              remove.mutate(item.id);
                          }}
                        >
                          Delete
                        </Button>
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          ) : (
            <Text>No campaigns yet.</Text>
          ))}
      </section>
    </Stack>
  );
}
