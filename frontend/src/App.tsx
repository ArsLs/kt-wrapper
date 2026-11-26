import {
  Alert,
  AppShell,
  Badge,
  Button,
  Container,
  Group,
  Stack,
  Title,
} from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { getCampaigns } from './api/campaigns';
import { getHealth } from './api/health';
import { CampaignsPage } from './campaigns/CampaignsPage';
import { CampaignEditor } from './campaigns/CampaignEditor';

export function App() {
  const health = useQuery({ queryKey: ['health'], queryFn: getHealth });
  const campaigns = useQuery({
    queryKey: ['campaigns'],
    queryFn: getCampaigns,
    refetchOnReconnect: false,
  });
  const path = window.location.pathname;
  const match = /^\/campaigns\/([^/]+)\/?$/.exec(path);
  const id = match && /^[1-9]\d*$/.test(match[1]) ? Number(match[1]) : null;
  const validId = id !== null && Number.isSafeInteger(id) ? id : null;
  return (
    <AppShell header={{ height: 56 }} padding="md">
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Title order={4}>Keitaro Wrapper</Title>
          <Badge
            color={health.isSuccess ? 'green' : health.isError ? 'red' : 'gray'}
          >
            API: {health.data?.status ?? (health.isError ? 'down' : '...')}
          </Badge>
        </Group>
      </AppShell.Header>
      <AppShell.Main>
        <Container size="lg" py="xl">
          {path === '/' ? (
            <CampaignsPage campaigns={campaigns} />
          ) : validId ? (
            <CampaignEditor campaignId={validId} campaigns={campaigns} />
          ) : (
            <Stack>
              <Alert color="red">Invalid page or campaign ID.</Alert>
              <Button component="a" href="/">
                Back to campaigns
              </Button>
            </Stack>
          )}
        </Container>
      </AppShell.Main>
    </AppShell>
  );
}
