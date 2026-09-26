import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { useQueryClient} from '@tanstack/react-query'
import './styles/tokens.css';
import './styles/global.css'
import './index.css'
import App from './App.tsx'

const queryClient = useQueryClient()
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClient client={queryClient}>
      <App />
    </QueryClient>
  </StrictMode>,
)
