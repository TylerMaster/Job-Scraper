package com.jobscraper.api.job;

import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.Optional;

@Service
@Transactional(readOnly = true)
public class JobService {

    private final JobRepository jobRepository;

    public JobService(JobRepository jobRepository) {
        this.jobRepository = jobRepository;
    }

    public Page<Job> findJobs(String keyword, Boolean remote, Pageable pageable) {
        return jobRepository.search(keyword, remote, pageable);
    }

    public Optional<Job> findById(Long id) {
        return jobRepository.findById(id);
    }
}
